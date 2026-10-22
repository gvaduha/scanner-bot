"""Reply decision strategies: weighted vocabulary or Claude."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from .config import BotConfig, ClaudeConfig, GroupConfig
from .http import HttpError, post_json
from .vocabulary import VocabularyScorer

log = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"

DECIDE_TOOL = {
    "name": "decide",
    "description": "Record whether the bot should reply to the message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {"type": "boolean", "description": "true if the bot should reply"},
            "reason": {"type": "string", "description": "short reason, one sentence"},
        },
        "required": ["reply", "reason"],
    },
}


@dataclass
class Decision:
    reply: bool
    source: str  # "vocabulary" | "claude" | "claude-error"
    reason: str = ""
    score: float | None = None


class VocabularyDecider:
    def __init__(self, group: GroupConfig):
        self.scorer = VocabularyScorer(group.vocabulary, group.alert_threshold, group.count_repeats)

    def decide(self, text: str) -> Decision:
        r = self.scorer.score(text)
        matched = ", ".join(f"{w}x{n}" if n > 1 else w for w, n in r.matches.items()) or "none"
        return Decision(
            reply=r.alert,
            source="vocabulary",
            reason=f"score {r.score:g} vs threshold {r.threshold:g}; matched: {matched}",
            score=r.score,
        )


PostFn = Callable[..., dict]


class ClaudeDecider:
    def __init__(self, cfg: ClaudeConfig, group: GroupConfig, post: PostFn = post_json):
        self.cfg = cfg
        self.group = group
        self._post = post
        self._fallback = VocabularyDecider(group) if group.vocabulary else None
        self._system = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        g = self.group
        parts = [self.cfg.system_prompt.strip(), "", f"Group: {g.label}"]
        if g.claude_instructions.strip():
            parts += ["Reply criteria:", g.claude_instructions.strip()]
        elif g.vocabulary:
            words = ", ".join(sorted(g.vocabulary, key=lambda w: -g.vocabulary[w]))
            parts += [
                "Reply criteria: the message is substantially about the topics "
                "signalled by these keywords (strongest first): " + words,
            ]
        else:
            parts += ["Reply criteria: none given; do not reply."]
        parts += ["", "Always answer by calling the `decide` tool."]
        return "\n".join(parts)

    def decide(self, text: str) -> Decision:
        payload = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": self._system,
            "tools": [DECIDE_TOOL],
            "tool_choice": {"type": "tool", "name": "decide"},
            "messages": [
                {"role": "user", "content": f"<message>\n{text}\n</message>"},
            ],
        }
        headers = {"x-api-key": self.cfg.api_key, "anthropic-version": ANTHROPIC_VERSION}
        try:
            resp = self._post(
                f"{self.cfg.api_base}/v1/messages", payload, headers=headers, timeout=self.cfg.timeout
            )
            for block in resp.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "decide":
                    inp = block.get("input") or {}
                    return Decision(
                        reply=bool(inp.get("reply")),
                        source="claude",
                        reason=str(inp.get("reason", "")),
                    )
            raise ValueError("response contained no decide tool call")
        except (HttpError, ValueError, KeyError, TypeError) as e:
            log.warning("Claude decision failed for %s: %s", self.group.label, e)
            return self._on_error(text, str(e))

    def _on_error(self, text: str, err: str) -> Decision:
        policy = self.cfg.on_error
        if policy == "vocabulary" and self._fallback:
            d = self._fallback.decide(text)
            d.source = "claude-error->vocabulary"
            return d
        return Decision(reply=(policy == "reply"), source="claude-error", reason=err)


def build_decider(cfg: BotConfig, group: GroupConfig, post: PostFn = post_json):
    if cfg.mode_for(group) == "claude":
        return ClaudeDecider(cfg.claude, group, post=post)
    return VocabularyDecider(group)
