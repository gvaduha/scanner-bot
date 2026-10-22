import json
import random
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from scanner_bot.bot import ScannerBot
from scanner_bot.config import parse_config
from scanner_bot.telegram import TelegramClient, TelegramError

CFG = {
    "telegram": {"token": "T", "poll_timeout": 1},
    "admin_user_ids": [42],
    "groups": [
        {"chat": -100, "title": "g1", "alert_threshold": 4,
         "vocabulary": {"scam": 3, "crypto*": 2}, "answers": ["A1", "A2"]},
        {"chat": "@pub_group", "alert_threshold": 0, "cooldown_seconds": 3600,
         "vocabulary": {"hi": 1}, "answers": ["hello"]},
    ],
}


def msg(text, chat_id=-100, mid=1, username=None, sender=7, is_bot=False, ctype="supergroup", **extra):
    chat = {"id": chat_id, "type": ctype}
    if username:
        chat["username"] = username
    return {"message_id": mid, "chat": chat, "from": {"id": sender, "is_bot": is_bot}, "text": text, **extra}


class FakeClient:
    def __init__(self):
        self.sent = []

    def reply(self, chat_id, message_id, text):
        self.sent.append((chat_id, message_id, text))


class HandleMessageTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.bot = ScannerBot(parse_config(CFG), self.client, rng=random.Random(0))
        self.bot.username = "scanbot"

    def test_alert_replies_to_triggering_message(self):
        self.bot.handle_message(msg("SCAM with cryptocurrency", mid=55))
        self.assertEqual(len(self.client.sent), 1)
        chat, mid, text = self.client.sent[0]
        self.assertEqual((chat, mid), (-100, 55))
        self.assertIn(text, ["A1", "A2"])

    def test_below_threshold_no_reply(self):
        self.bot.handle_message(msg("just scam"))  # 3 <= 4
        self.assertEqual(self.client.sent, [])

    def test_unconfigured_chat_ignored(self):
        self.bot.handle_message(msg("scam scam scam", chat_id=-999))
        self.assertEqual(self.client.sent, [])

    def test_caption_is_scanned(self):
        m = msg("", mid=3)
        del m["text"]
        m["caption"] = "scam crypto"
        self.bot.handle_message(m)
        self.assertEqual(len(self.client.sent), 1)

    def test_bots_ignored(self):
        self.bot.handle_message(msg("scam scam", is_bot=True))
        self.assertEqual(self.client.sent, [])

    def test_group_by_username_and_cooldown(self):
        self.bot.handle_message(msg("hi", chat_id=-5, username="Pub_Group", mid=1))
        self.bot.handle_message(msg("hi again", chat_id=-5, username="pub_group", mid=2))
        self.assertEqual(self.client.sent, [(-5, 1, "hello")])

    def test_dry_run(self):
        self.bot.cfg.dry_run = True
        self.bot.handle_message(msg("scam scam"))
        self.assertEqual(self.client.sent, [])

    def test_chatid_command(self):
        self.bot.handle_message(msg("/chatid", chat_id=-777, ctype="group"))
        self.bot.handle_message(msg("/chatid@otherbot", chat_id=-777))
        self.assertEqual(self.client.sent, [(-777, 1, "chat id: -777")])

    def test_commands_not_scanned(self):
        self.bot.handle_message(msg("/start scam scam scam"))
        self.assertEqual(self.client.sent, [])

    def test_reload_admin_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            new = json.loads(json.dumps(CFG))
            new["groups"][0]["alert_threshold"] = 100
            p.write_text(yaml.safe_dump(new))
            self.bot.config_path = p
            self.bot.handle_message(msg("/reload", sender=7))
            self.assertEqual(self.bot.cfg.groups[0].alert_threshold, 4)
            self.bot.handle_message(msg("/reload", sender=42, mid=9))
            self.assertEqual(self.bot.cfg.groups[0].alert_threshold, 100)
            self.assertIn("reloaded", self.client.sent[-1][2])
            p.write_text("groups: broken")
            self.bot.handle_message(msg("/reload", sender=42, mid=10))
            self.assertIn("failed", self.client.sent[-1][2])
            self.assertEqual(self.bot.cfg.groups[0].alert_threshold, 100)


class FakeTelegram(BaseHTTPRequestHandler):
    """Fake Bot API: serves queued updates, records sendMessage calls."""

    updates: list = []
    sent: list = []
    fail_send_once = False

    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        method = self.path.rsplit("/", 1)[-1]
        params = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if not self.path.startswith("/botT/"):
            return self._reply({"ok": False, "description": "Unauthorized"}, 401)
        if method == "getMe":
            return self._reply({"ok": True, "result": {"id": 1, "username": "scanbot",
                                                        "can_read_all_group_messages": True}})
        if method == "getUpdates":
            offset = params.get("offset") or 0
            pending = [u for u in FakeTelegram.updates if u["update_id"] >= offset]
            if not pending:
                time.sleep(0.05)
            return self._reply({"ok": True, "result": pending})
        if method == "sendMessage":
            if FakeTelegram.fail_send_once:
                FakeTelegram.fail_send_once = False
                return self._reply({"ok": False, "description": "Too Many Requests",
                                    "parameters": {"retry_after": 0.01}}, 429)
            FakeTelegram.sent.append(params)
            return self._reply({"ok": True, "result": {"message_id": 999}})
        return self._reply({"ok": False, "description": "unknown method"}, 404)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        FakeTelegram.sent = []
        FakeTelegram.fail_send_once = True
        FakeTelegram.updates = [
            {"update_id": 10, "message": msg("hello everyone", mid=1)},
            {"update_id": 11, "message": msg("crypto scam, crypto!", mid=2)},
            {"update_id": 12, "message": msg("scam scam", chat_id=-3, mid=3)},
        ]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_polls_and_replies(self):
        cfg = parse_config(CFG)
        bot = ScannerBot(cfg, TelegramClient("T", self.base))
        t = threading.Thread(target=bot.run, daemon=True)
        t.start()
        deadline = time.time() + 5
        while not FakeTelegram.sent and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        bot.stop()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(FakeTelegram.sent), 1)  # sent after one 429 retry
        sent = FakeTelegram.sent[0]
        self.assertEqual(sent["chat_id"], -100)
        self.assertEqual(sent["reply_parameters"]["message_id"], 2)
        self.assertIn(sent["text"], ["A1", "A2"])

    def test_bad_token(self):
        with self.assertRaises(TelegramError):
            TelegramClient("wrong", self.base).get_me()


if __name__ == "__main__":
    unittest.main()
