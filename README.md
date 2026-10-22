# Scanner bot

A Telegram bot that reads the groups you configure and decides, for every
message, whether to reply. When it decides to, it replies to that exact message
with a random text from the group's answer list.

The decision is made in one of two ways, chosen globally or per group:

- **Vocabulary**: every word in the group's vocabulary has a weight. The
  weights of the words found in a message are added up, and if the sum is
  **greater than** the group's `alert_threshold`, the bot replies.
- **Claude**: the message is sent to Claude with the group's criteria, and
  Claude says yes or no. If the Claude call fails, the bot can fall back to the
  vocabulary, skip, or reply anyway (`claude.on_error`).

The only dependency is PyYAML. Everything else uses the Python standard library
and calls the Telegram Bot API and the Anthropic Messages API directly.

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. **Disable privacy mode**: in BotFather send `/setprivacy`, pick the bot,
   choose `Disable`. Without this a bot only sees commands and replies to
   itself. (Making the bot a group admin also works.) If the bot was already in
   a group, remove it and add it back after changing this.
3. Add the bot to your groups.
4. Install and configure:

   ```bash
   git clone https://github.com/gvaduha/scanner-bot.git
   cd scanner-bot
   python -m pip install -r requirements.txt     # Python 3.10+
   cp config.example.yaml config.yaml            # Windows: copy config.example.yaml config.yaml
   ```

5. Find each group's id: with `chatid_command: true` (the default), send
   `/chatid` in the group and the bot replies with it. Unknown groups are also
   logged when the bot is running. Public groups can be given as `@username`.
6. Edit `config.yaml`, set the secrets and run:

   ```bash
   export TELEGRAM_BOT_TOKEN=123456:ABC...        # PowerShell: $env:TELEGRAM_BOT_TOKEN="123456:ABC..."
   export ANTHROPIC_API_KEY=sk-ant-...            # only needed for claude mode
   python -m scanner_bot -c config.yaml --check  # validate the config
   python -m scanner_bot -c config.yaml --dry-run  # watch decisions without replying
   python -m scanner_bot -c config.yaml          # run for real
   ```

## Configuration

See [`config.example.yaml`](config.example.yaml); every option is commented.
`${VAR}` in any value is replaced with an environment variable, and
`${VAR:-default}` uses a default when the variable is not set.

```yaml
mode: vocabulary              # default for all groups: vocabulary | claude
groups:
  - chat: -1001234567890      # chat id or "@public_group"
    title: My group
    alert_threshold: 5        # reply when score > 5
    cooldown_seconds: 60      # optional: at most one reply per minute here
    vocabulary:
      scam: 3                 # exact word
      crypto*: 2              # prefix: crypto, cryptocurrency, ...
      "free money": 4         # phrase
      news: -2                # negative weights lower the score
    answers:
      - "Careful, this looks like a scam."
      - "Please don't send money to strangers."

  - chat: "@other_group"
    mode: claude              # this group asks Claude
    claude_instructions: |
      Reply when the message advertises paid services or crypto schemes.
    answers:
      - "Advertising is not allowed here."
```

### Vocabulary matching

- Matching is case-insensitive and on whole words; `ё` is treated as `е`.
- `word*` matches every word that starts with `word`, which helps with
  inflected languages (`скидк*` matches скидка, скидки, скидку...).
- An entry with spaces is a phrase: its words must appear next to each other.
- By default every occurrence counts (`scam scam` = 2 × weight). Set
  `count_repeats: false` on a group to count each entry once per message.
- Text messages and media captions are scanned. Messages from other bots and
  commands addressed to this bot are ignored.

### Claude mode

The bot sends the message to the Messages API with a system prompt built from
`claude.system_prompt` and the group's `claude_instructions` (or, if a group
has none, its vocabulary words as topic hints), and forces a `decide` tool
call that returns `reply: true|false` plus a reason. The reply text still comes
from the group's `answers` list. Set the model with `claude.model`; a small
model is usually enough for this and keeps cost down.

Every message in a Claude-mode group is one API call. On busy groups, consider
vocabulary mode, or a cheap model.

## Commands and tools

| What | How |
|---|---|
| Show a chat's id | send `/chatid` in the chat (turn off with `chatid_command: false`) |
| Reload config without restart | send `/reload` from a user listed in `admin_user_ids` |
| Validate config | `python -m scanner_bot -c config.yaml --check` |
| Test a text offline | `python -m scanner_bot -c config.yaml --test "some text" [--group -100...]` |
| Never send, only log | `--dry-run` or `dry_run: true` |

`--test` prints the score and matched words per group, which is the quickest
way to tune weights and thresholds.

## Running with Docker

```bash
docker build -t scanner-bot .
docker run -d --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=... -e ANTHROPIC_API_KEY=... \
  -v "$PWD/config.yaml:/app/config.yaml:ro" scanner-bot
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

The tests cover scoring, config validation, both deciders (with a mocked
Anthropic API) and an end-to-end run against a local fake Telegram server.

## Notes

- One bot token can be polled by only one running instance at a time.
- Telegram limits bots to about 20 messages per minute per group; use
  `cooldown_seconds` on busy groups. When Telegram asks the bot to slow down,
  it waits the requested `retry_after` time and tries again.
