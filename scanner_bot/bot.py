"""The scanner bot: polls Telegram, decides per message, replies on alerts."""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import BotConfig, ConfigError, GroupConfig, load_config
from .deciders import Decision, build_decider
from .http import post_json
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)


class ScannerBot:
    def __init__(
        self,
        cfg: BotConfig,
        client: TelegramClient,
        config_path: str | Path | None = None,
        post=post_json,
        rng: random.Random | None = None,
        workers: int = 8,
    ):
        self.client = client
        self.config_path = config_path
        self._post = post
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._last_reply: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="msg")
        self._stop = threading.Event()
        self.username: str = ""
        self.bot_id: int | None = None
        self._apply_config(cfg)

    # ------------------------------------------------------------ config
    def _apply_config(self, cfg: BotConfig) -> None:
        deciders = {str(g.chat): build_decider(cfg, g, post=self._post) for g in cfg.groups}
        with self._lock:
            self.cfg = cfg
            self._deciders = deciders
        for g in cfg.groups:
            log.info("watching %s (mode=%s, threshold=%g, %d words, %d answers)",
                     g.label, cfg.mode_for(g), g.alert_threshold, len(g.vocabulary), len(g.answers))

    def reload(self) -> str:
        if not self.config_path:
            return "No config path to reload from."
        try:
            new = load_config(self.config_path)
        except ConfigError as e:
            log.error("reload failed: %s", e)
            return f"Reload failed, keeping old config: {e}"
        new.dry_run = new.dry_run or self.cfg.dry_run
        self._apply_config(new)
        return f"Config reloaded: {len(new.groups)} group(s)."

    # ------------------------------------------------------------ handling
    def _command(self, text: str) -> str | None:
        """Return the command name if the text is a command for this bot."""
        if not text.startswith("/"):
            return None
        head = text.split(maxsplit=1)[0][1:]
        name, _, target = head.partition("@")
        if target and self.username and target.lower() != self.username.lower():
            return None
        return name.lower()

    def handle_message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        chat_id = chat.get("id")
        text = msg.get("text") or msg.get("caption") or ""
        if chat_id is None or not text:
            return

        cfg = self.cfg
        cmd = self._command(text)
        if cmd == "chatid" and cfg.chatid_command:
            self._send(chat_id, msg["message_id"],
                       f"chat id: {chat_id}" + (f"\nusername: @{chat['username']}" if chat.get("username") else ""))
            return
        if cmd == "reload" and sender.get("id") in cfg.admin_user_ids:
            self._send(chat_id, msg["message_id"], self.reload())
            return

        group = cfg.find_group(chat_id, chat.get("username"))
        if group is None:
            if chat.get("type") in ("group", "supergroup"):
                log.info("message from unconfigured chat %s (%s) ignored", chat_id, chat.get("title"))
            return
        if cmd is not None:
            return  # commands addressed to the bot are not scanned
        if cfg.ignore_bots and sender.get("is_bot"):
            return

        decider = self._deciders.get(str(group.chat))
        decision: Decision = decider.decide(text)
        log.info("[%s] msg %s from %s: reply=%s (%s: %s)", group.label, msg.get("message_id"),
                 sender.get("username") or sender.get("id"), decision.reply, decision.source, decision.reason)
        if not decision.reply:
            return
        if not self._cooldown_ok(group):
            log.info("[%s] alert suppressed by cooldown", group.label)
            return
        answer = self._rng.choice(group.answers)
        if cfg.dry_run:
            log.info("[%s] DRY RUN, would reply: %s", group.label, answer)
            return
        self._send(chat_id, msg["message_id"], answer)

    def _cooldown_ok(self, group: GroupConfig) -> bool:
        if group.cooldown_seconds <= 0:
            return True
        key, now = str(group.chat), time.monotonic()
        with self._lock:
            last = self._last_reply.get(key)
            if last is not None and now - last < group.cooldown_seconds:
                return False
            self._last_reply[key] = now
            return True

    def _send(self, chat_id: int, message_id: int, text: str) -> None:
        for attempt in range(3):
            try:
                self.client.reply(chat_id, message_id, text)
                return
            except TelegramError as e:
                if e.retry_after and attempt < 2:
                    time.sleep(float(e.retry_after))
                    continue
                log.error("failed to reply in %s: %s", chat_id, e)
                return

    def _safe_handle(self, msg: dict) -> None:
        try:
            self.handle_message(msg)
        except Exception:  # never let one message kill the worker
            log.exception("error handling message %s", msg.get("message_id"))

    # ------------------------------------------------------------ loop
    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        me = self.client.get_me()
        self.username, self.bot_id = me.get("username", ""), me.get("id")
        log.info("started as @%s%s", self.username, " (dry run)" if self.cfg.dry_run else "")
        if not me.get("can_read_all_group_messages"):
            log.warning("privacy mode is ON: the bot only sees commands and replies to itself "
                        "unless it is a group admin. Disable it in @BotFather: /setprivacy -> Disable")
        offset: int | None = None
        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(offset, self.cfg.telegram.poll_timeout)
                backoff = 1.0
            except TelegramError as e:
                wait = float(e.retry_after or backoff)
                log.warning("getUpdates failed: %s; retrying in %.0fs", e, wait)
                self._stop.wait(wait)
                backoff = min(backoff * 2, 60)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if msg:
                    self._pool.submit(self._safe_handle, msg)
        self._pool.shutdown(wait=True)
