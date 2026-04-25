"""Profanity detection.

Two-tier matcher:
  1. Phrase scanner — greedy longest-match against a multi-word phrase set.
     Catches things like "oh my god", "what the fuck", or compounds Whisper
     splits ("bull shit") that the single-word matcher would miss or over-fire on.
  2. Single-word matcher — O(1) set lookup per token, with suffix stripping for
     common conjugations ("fucking" -> "fuck", "fuckin'" -> "fuck", "bitches" -> "bitch").

Each ProfanityHit carries a `span` (start_word_idx, end_word_idx_exclusive) so the
SRT writer knows which words a phrase consumed and emits the phrase replacement
once instead of word-by-word. EDL just sees a single mute region per hit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .transcribe import Word

# Strip leading/trailing punctuation but keep internal apostrophes/hyphens.
_TRIM = re.compile(r"^[^\w']+|[^\w']+$")
# Suffix variants so "fuckin'" matches "fucking" / "fuckers" without enumerating every conjugation.
_SUFFIX_VARIANTS = ("ing", "in'", "in", "ed", "er", "ers", "y", "s", "'s", "'d", "'ll", "'ve")


@dataclass
class ProfanityHit:
    word: Word                 # synthetic word spanning the entire hit (start of first, end of last)
    matched: str               # the entry that matched (single swear or whole phrase)
    context: str               # surrounding sentence with the swear underscored
    span: tuple[int, int]      # [start, end_exclusive) in the original word list
    is_phrase: bool = False


@dataclass
class PhraseSet:
    """Compiled phrase index: phrases bucketed by length for fast window matching."""
    by_length: dict[int, set[tuple[str, ...]]] = field(default_factory=dict)
    max_length: int = 0
    canonical: dict[tuple[str, ...], str] = field(default_factory=dict)


def normalize(word_text: str) -> str:
    return _TRIM.sub("", word_text.lower()).strip("'")


def _strip_apostrophes(t: str) -> str:
    return t.replace("'", "")


def match(token: str, swears: set[str]) -> str | None:
    if not token:
        return None
    if token in swears:
        return token
    for suf in _SUFFIX_VARIANTS:
        if token.endswith(suf) and len(token) > len(suf) + 1:
            base = token[: -len(suf)]
            if base in swears:
                return base
    return None


def compile_phrases(phrase_lines: list[str]) -> PhraseSet:
    """Build a PhraseSet from raw phrase strings.

    For each phrase we also index a no-apostrophe variant (so "for god's sake"
    matches "for gods sake" too — Whisper sometimes drops apostrophes). The
    canonical mapping points back to the human-readable original so replacement
    lookup uses the same key as the JSON.
    """
    ps = PhraseSet()
    for raw in phrase_lines:
        canonical = " ".join(raw.split())
        tokens = tuple(normalize(t) for t in raw.split())
        if not all(tokens):
            continue
        n = len(tokens)
        ps.by_length.setdefault(n, set()).add(tokens)
        ps.canonical[tokens] = canonical
        ps.max_length = max(ps.max_length, n)
        alt = tuple(_strip_apostrophes(t) for t in tokens)
        if alt != tokens:
            ps.by_length.setdefault(n, set()).add(alt)
            ps.canonical[alt] = canonical
    return ps


def detect(words: list[Word], swears: set[str], phrases: PhraseSet | None = None,
           context_window: int = 6) -> list[ProfanityHit]:
    """Walk the word stream emitting one ProfanityHit per swear or phrase match."""
    hits: list[ProfanityHit] = []
    n_words = len(words)
    i = 0
    while i < n_words:
        # 1. Try phrase match (longest first).
        consumed = 0
        phrase_canonical = None
        if phrases and phrases.max_length >= 2:
            max_try = min(phrases.max_length, n_words - i)
            for length in range(max_try, 1, -1):
                window = tuple(normalize(words[i + k].text) for k in range(length))
                if any(not t for t in window):
                    continue
                bucket = phrases.by_length.get(length)
                if not bucket:
                    continue
                if window in bucket:
                    phrase_canonical = phrases.canonical[window]
                    consumed = length
                    break
                window_alt = tuple(_strip_apostrophes(t) for t in window)
                if window_alt in bucket:
                    phrase_canonical = phrases.canonical[window_alt]
                    consumed = length
                    break

        if consumed:
            first = words[i]
            last = words[i + consumed - 1]
            synthetic = Word(
                text=phrase_canonical or " ".join(words[i + k].text for k in range(consumed)),
                start=first.start,
                end=last.end,
                score=min(words[i + k].score for k in range(consumed)),
            )
            hits.append(ProfanityHit(
                word=synthetic,
                matched=phrase_canonical or "",
                context=_context(words, i, i + consumed - 1, context_window),
                span=(i, i + consumed),
                is_phrase=True,
            ))
            i += consumed
            continue

        # 2. Try single-word match.
        token = normalize(words[i].text)
        matched = match(token, swears)
        if matched:
            w = words[i]
            hits.append(ProfanityHit(
                word=w,
                matched=matched,
                context=_context(words, i, i, context_window),
                span=(i, i + 1),
                is_phrase=False,
            ))
        i += 1
    return hits


def _context(words: list[Word], hit_lo: int, hit_hi: int, window: int) -> str:
    """Build a marked-up context string with the matched span underscored."""
    lo = max(0, hit_lo - window)
    hi = min(len(words), hit_hi + window + 1)
    parts: list[str] = []
    in_hit = False
    for j in range(lo, hi):
        t = words[j].text.strip()
        if hit_lo <= j <= hit_hi:
            if not in_hit:
                parts.append(f"_{t}")
                in_hit = True
            else:
                parts[-1] += f" {t}"
            if j == hit_hi:
                parts[-1] += "_"
                in_hit = False
        else:
            parts.append(t)
    return " ".join(parts)


def replacement_for(matched: str, mapping: dict[str, str], default: str) -> str:
    """Look up a replacement, falling back to the default placeholder."""
    return mapping.get(matched, default)
