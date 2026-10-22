import unittest

from scanner_bot.config import parse_config
from scanner_bot.deciders import ClaudeDecider, VocabularyDecider, build_decider
from scanner_bot.http import HttpError


def make_cfg(**group):
    g = {"chat": -1, "answers": ["x"], "alert_threshold": 2, "vocabulary": {"scam": 3}}
    g.update(group)
    return parse_config({
        "telegram": {"token": "T"},
        "claude": {"api_key": "KEY", "model": "test-model"},
        "groups": [g],
    })


class FakePost:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def __call__(self, url, payload, headers=None, timeout=None):
        self.calls.append((url, payload, headers))
        if self.error:
            raise self.error
        return self.response


def tool_response(reply, reason="because"):
    return {"content": [{"type": "tool_use", "name": "decide", "input": {"reply": reply, "reason": reason}}]}


class DeciderTest(unittest.TestCase):
    def test_vocabulary(self):
        cfg = make_cfg()
        d = build_decider(cfg, cfg.groups[0])
        self.assertIsInstance(d, VocabularyDecider)
        self.assertTrue(d.decide("total scam").reply)
        self.assertFalse(d.decide("hello").reply)

    def test_claude_request_and_decision(self):
        cfg = make_cfg(mode="claude", claude_instructions="Reply to spam.")
        post = FakePost(tool_response(True, "spam"))
        d = build_decider(cfg, cfg.groups[0], post=post)
        self.assertIsInstance(d, ClaudeDecider)
        res = d.decide("buy now")
        self.assertTrue(res.reply)
        self.assertEqual(res.source, "claude")
        url, payload, headers = post.calls[0]
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(headers["x-api-key"], "KEY")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["tool_choice"], {"type": "tool", "name": "decide"})
        self.assertIn("Reply to spam.", payload["system"])
        self.assertIn("buy now", payload["messages"][0]["content"])

    def test_claude_no(self):
        cfg = make_cfg(mode="claude")
        d = build_decider(cfg, cfg.groups[0], post=FakePost(tool_response(False)))
        self.assertFalse(d.decide("hi").reply)

    def test_vocabulary_used_as_hint_without_instructions(self):
        cfg = make_cfg(mode="claude")
        post = FakePost(tool_response(False))
        build_decider(cfg, cfg.groups[0], post=post).decide("hi")
        self.assertIn("scam", post.calls[0][1]["system"])

    def test_claude_error_falls_back_to_vocabulary(self):
        cfg = make_cfg(mode="claude")
        d = build_decider(cfg, cfg.groups[0], post=FakePost(error=HttpError(500, "boom")))
        res = d.decide("scam here")
        self.assertTrue(res.reply)
        self.assertEqual(res.source, "claude-error->vocabulary")

    def test_claude_error_policies(self):
        for policy, expected in (("skip", False), ("reply", True)):
            cfg = make_cfg(mode="claude")
            cfg.claude.on_error = policy
            d = build_decider(cfg, cfg.groups[0], post=FakePost(error=HttpError(None, "down")))
            self.assertEqual(d.decide("scam").reply, expected)

    def test_malformed_response(self):
        cfg = make_cfg(mode="claude")
        cfg.claude.on_error = "skip"
        d = build_decider(cfg, cfg.groups[0], post=FakePost({"content": [{"type": "text", "text": "yes"}]}))
        self.assertEqual(d.decide("x").source, "claude-error")


if __name__ == "__main__":
    unittest.main()
