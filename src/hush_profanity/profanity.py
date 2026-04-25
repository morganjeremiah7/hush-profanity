"""Profanity detection.

Matches each transcribed word against a swear set in O(1) per word, after stripping
surrounding punctuation. Handles common contractions ("fuckin'", "fuck's") by also
testing the word with trailing 'ing/'in/'s/'d removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .transcribe import Word

# Strip leading/trailing punctuation but keep internal apostrophes/hyphens.
_TRIM = re.compile(r"^[^\w']+|[^\w']+$")
# Suffix variants so "fuckin'" matches "fucking" and "fuckers" without enumerating every conjugation
# in the swears file.
_SUFFIX_VARIANTS = ("ing", "in'", "in", "ed", "er", "ers", "y", "s", "'s", "'d", "'ll", "'ve")


@dataclass
class ProfanityHit:
    word: Word
    matched: str  # the swear-list entry that matched
    context: str  # surrounding sentence with the swear underscored


def normalize(word_text: str) -> str:
    return _TRIM.sub("", word_text.lower()).strip("'")


def match(token: str, swears: set[str]) -> str | None:
    if not token:
        return None
    if token in swears:
        return token
    # Try removing common suffixes — "fuckin'" -> "fuck", "fuckers" -> "fucker" -> "fuck"
    for suf in _SUFFIX_VARIANTS:
        if token.endswith(suf) and len(token) > len(suf) + 1:
            base = token[: -len(suf)]
            if base in swears:
                return base
    return None


def detect(words: list[Word], swears: set[str], context_window: int = 6) -> list[ProfanityHit]:
    """Walk the word stream and emit one ProfanityHit per swear match."""
    hits: list[ProfanityHit] = []
    for i, w in enumerate(words):
        token = normalize(w.text)
        matched = match(token, swears)
        if not matched:
            continue
        lo = max(0, i - context_window)
        hi = min(len(words), i + context_window + 1)
        context_parts = []
        for j in range(lo, hi):
            t = words[j].text.strip()
            if j == i:
                context_parts.append(f"_{t}_")
            else:
                context_parts.append(t)
        hits.append(ProfanityHit(
            word=w,
            matched=matched,
            context=" ".join(context_parts),
        ))
    return hits


def replacement_for(matched: str, mapping: dict[str, str], default: str) -> str:
    """Look up a replacement, falling back to the default placeholder."""
    return mapping.get(matched, default)
