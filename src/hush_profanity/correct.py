"""Subtitle correction.

When an official .srt is found alongside a video, we use it to fix Whisper
transcription errors while keeping Whisper's per-word timing.

Strategy:
    1. Tokenize the official .srt into a flat word stream (cue text only — drop
       stage directions in [brackets] and lyrics in <i>tags</i>).
    2. Diff Whisper words vs. official words at the word level using
       difflib.SequenceMatcher (LCS-style).
    3. For "equal" runs: take the official spelling, keep Whisper's timestamp.
    4. For "replace" / "insert" / "delete" runs: prefer the official text and
       interpolate timestamps proportionally across the Whisper span the run
       covered.

We deliberately do NOT use the official text for profanity detection — official
subs are often bowdlerized ("f--k", "[bleeped]") and would miss matches. Profanity
is always detected from raw Whisper output. The corrected stream is only used to
write a more readable cleaned .srt.
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

from .transcribe import Word

log = logging.getLogger(__name__)

_TIMECODE_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")
_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_WORD_SPLIT_RE = re.compile(r"\s+")
_NORM_RE = re.compile(r"[^\w']")


def find_official_subtitle(video: Path, suffixes: list[str]) -> Path | None:
    """Return the best official .srt next to `video`, or None."""
    candidates: list[Path] = []
    base = video.with_suffix("")
    candidates.append(base.with_suffix(".srt"))
    stem = video.stem
    parent = video.parent
    for suf in suffixes:
        candidates.append(parent / f"{stem}{suf}")
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    return None


def parse_srt_words(path: Path) -> list[tuple[str, float, float]]:
    """Return (word_text, cue_start, cue_end) tuples for every word in the .srt.

    Strips HTML-ish tags, [bracketed] stage directions, and (parenthetical) speaker
    labels. The cue start/end aren't precise per-word timing, but they're useful as
    bounds when we have to interpolate.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # Normalize line endings
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    out: list[tuple[str, float, float]] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # First line might be the index (digits only); the timing line has " --> "
        timing_line = next((ln for ln in lines if "-->" in ln), None)
        if not timing_line:
            continue
        try:
            left, right = timing_line.split("-->")
            start = _parse_ts(left.strip())
            end = _parse_ts(right.strip().split()[0])
        except Exception:
            continue
        body_lines = [ln for ln in lines if "-->" not in ln and not ln.strip().isdigit()]
        body = " ".join(body_lines)
        body = _TAG_RE.sub("", body)
        body = _BRACKET_RE.sub("", body)
        for w in _WORD_SPLIT_RE.split(body):
            if w.strip():
                out.append((w.strip(), start, end))
    return out


def _parse_ts(s: str) -> float:
    m = _TIMECODE_RE.match(s)
    if not m:
        raise ValueError(f"bad timecode: {s!r}")
    h, mm, ss, ms = (int(g) for g in m.groups())
    return h * 3600 + mm * 60 + ss + ms / 1000.0


def _norm(token: str) -> str:
    return _NORM_RE.sub("", token.lower()).strip("'")


def correct_words(whisper_words: list[Word], official: list[tuple[str, float, float]]) -> list[Word]:
    """Return a corrected word stream: official spelling + Whisper-derived timing.

    The returned list is approximately the length of `official`. Where official
    inserted text without a Whisper counterpart, timing is interpolated from the
    surrounding Whisper anchors (or the official cue bounds as a fallback).
    """
    if not whisper_words or not official:
        return whisper_words

    w_norm = [_norm(w.text) for w in whisper_words]
    o_norm = [_norm(t) for t, _, _ in official]
    matcher = SequenceMatcher(a=w_norm, b=o_norm, autojunk=False)
    corrected: list[Word] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                w = whisper_words[i1 + k]
                official_text = official[j1 + k][0]
                corrected.append(Word(text=official_text, start=w.start, end=w.end, score=w.score))
        else:
            # Find timing anchors: end of the previous Whisper match, start of the next.
            prev_end = whisper_words[i1 - 1].end if i1 > 0 else (
                whisper_words[i1].start if i1 < len(whisper_words) else 0.0
            )
            next_start = whisper_words[i2].start if i2 < len(whisper_words) else (
                whisper_words[-1].end if whisper_words else prev_end
            )
            # Fallback to the official cue bounds if Whisper anchors are missing/zero-width.
            if next_start <= prev_end and j1 < len(official):
                _, cue_s, cue_e = official[j1]
                prev_end = max(prev_end, cue_s)
                next_start = max(prev_end + 0.01, cue_e)

            n = max(1, j2 - j1)
            span = max(0.0, next_start - prev_end)
            step = span / n if n else 0.0
            for k in range(n):
                official_text = official[j1 + k][0]
                start = prev_end + step * k
                end = prev_end + step * (k + 1)
                corrected.append(Word(text=official_text, start=start, end=end, score=0.5))

    return corrected
