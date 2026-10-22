"""Weighted-vocabulary scoring.

Vocabulary entries:

* ``word``        - matches the word exactly (case-insensitive)
* ``pref*``       - matches any word starting with ``pref`` (handy for
                    inflected languages: ``скидк*`` matches скидка, скидки, ...)
* ``two words``   - a phrase: matches the words appearing next to each other;
                    every word of a phrase may also end with ``*``

Each match adds the entry's weight to the score. With ``count_repeats`` on
(default), every occurrence counts; otherwise each entry counts at most once.
An alert fires when the score is strictly greater than the threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def normalize(text: str) -> str:
    return text.casefold().replace("ё", "е")


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


@dataclass(frozen=True)
class _Pattern:
    entry: str
    parts: tuple[tuple[str, bool], ...]  # (stem, is_prefix)
    weight: float

    def _part_matches(self, token: str, i: int) -> bool:
        stem, prefix = self.parts[i]
        return token.startswith(stem) if prefix else token == stem

    def count(self, tokens: list[str]) -> int:
        n = len(self.parts)
        hits = 0
        for start in range(len(tokens) - n + 1):
            if all(self._part_matches(tokens[start + i], i) for i in range(n)):
                hits += 1
        return hits


def _compile(entry: str, weight: float) -> _Pattern | None:
    parts = []
    for raw in entry.split():
        prefix = raw.endswith("*")
        words = tokenize(raw.rstrip("*"))
        if not words:
            continue
        # Punctuation inside an entry ("e-mail") splits into several tokens.
        for j, w in enumerate(words):
            parts.append((w, prefix and j == len(words) - 1))
    if not parts:
        return None
    return _Pattern(entry, tuple(parts), weight)


@dataclass
class ScoreResult:
    score: float
    threshold: float
    matches: dict[str, int] = field(default_factory=dict)  # entry -> hit count

    @property
    def alert(self) -> bool:
        return self.score > self.threshold


class VocabularyScorer:
    def __init__(self, vocabulary: dict[str, float], threshold: float, count_repeats: bool = True):
        self.threshold = threshold
        self.count_repeats = count_repeats
        self._patterns = [p for p in (_compile(w, wt) for w, wt in vocabulary.items()) if p]

    def score(self, text: str) -> ScoreResult:
        tokens = tokenize(text)
        total = 0.0
        matches: dict[str, int] = {}
        for p in self._patterns:
            hits = p.count(tokens)
            if not hits:
                continue
            if not self.count_repeats:
                hits = 1
            matches[p.entry] = hits
            total += p.weight * hits
        return ScoreResult(score=total, threshold=self.threshold, matches=matches)
