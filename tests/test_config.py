import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scanner_bot.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent


def base(**over):
    cfg = {
        "telegram": {"token": "T"},
        "groups": [{"chat": -100, "alert_threshold": 1, "vocabulary": {"a": 2}, "answers": ["hi"]}],
    }
    cfg.update(over)
    return cfg


class ConfigTest(unittest.TestCase):
    def test_minimal(self):
        c = parse_config(base())
        self.assertEqual(c.mode, "vocabulary")
        self.assertEqual(c.groups[0].chat, -100)
        self.assertEqual(c.groups[0].vocabulary, {"a": 2.0})

    def test_env_expansion(self):
        with mock.patch.dict(os.environ, {"TOK": "secret"}):
            c = parse_config(base(telegram={"token": "${TOK}"}))
        self.assertEqual(c.telegram.token, "secret")

    def test_env_default_and_missing(self):
        os.environ.pop("NOPE_VAR", None)
        c = parse_config(base(claude={"api_key": "${NOPE_VAR:-}"}))
        self.assertEqual(c.claude.api_key, "")
        with self.assertRaises(ConfigError):
            parse_config(base(telegram={"token": "${NOPE_VAR}"}))

    def test_chat_forms(self):
        g = {"vocabulary": {"a": 1}, "answers": ["x"]}
        c = parse_config(base(groups=[{**g, "chat": "-1001"}, {**g, "chat": "@My_Group"}]))
        self.assertEqual(c.groups[0].chat, -1001)
        self.assertIs(c.find_group(-1001, None), c.groups[0])
        self.assertIs(c.find_group(5, "my_group"), c.groups[1])
        self.assertIsNone(c.find_group(5, "other"))

    def test_errors(self):
        bad = [
            base(telegram={}),
            base(mode="magic"),
            base(groups=[]),
            base(groups=[{"chat": -1, "vocabulary": {"a": 1}, "answers": []}]),
            base(groups=[{"chat": -1, "answers": ["x"]}]),  # vocabulary mode, no words
            base(groups=[{"chat": -1, "vocabulary": {"a": "heavy"}, "answers": ["x"]}]),
            base(groups=[{"chat": "not a chat", "vocabulary": {"a": 1}, "answers": ["x"]}]),
            base(mode="claude"),  # no api key
            base(groups=[{"chat": -1, "vocabulary": {"a": 1}, "answers": ["x"]}] * 2),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                parse_config(data)

    def test_group_mode_override(self):
        c = parse_config(base(
            claude={"api_key": "k"},
            groups=[{"chat": -1, "mode": "claude", "answers": ["x"]}],
        ))
        self.assertEqual(c.mode_for(c.groups[0]), "claude")

    def test_example_config_loads(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "ANTHROPIC_API_KEY": "k"}):
            c = load_config(ROOT / "config.example.yaml")
        self.assertEqual(len(c.groups), 2)
        self.assertEqual(c.mode_for(c.groups[1]), "claude")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ConfigError):
            load_config(Path(d) / "none.yaml")


if __name__ == "__main__":
    unittest.main()
