"""Subtitle (.srt) generation.

The cue builder honors three things to avoid the common bad-output patterns:

  1. Sentence boundaries. When a word ends with `.`, `!`, or `?`, finalize the
     current cue (so the next sentence starts a new cue). An abbreviation guard
     skips this for short words ending in `.` (Mr., Dr., U.S., p.m.) so they
     don't fragment the cue mid-sentence.

  2. Long inter-word silence. If the gap between this word's end and the next
     word's start exceeds `max_pause_seconds`, finalize the cue. Without this,
     a 20-second pause inside the audio would stretch one cue across the gap.

  3. Capped read-time tail. Each finalized cue's display end is set to
     `last_word.end + tail_seconds` (and clamped so it doesn't crowd the next
     cue). Without this, a cue's `end` could stay glued to the start of the
     next word — which is sometimes seconds or minutes away.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from .profanity import PhraseSet, detect, replacement_for
from .transcribe import Word


SENTENCE_TERMINATORS = (".", "!", "?")
# Trailing chars to peel off before sentence-end check (closing quote, paren, etc.).
TRAILING_PUNCT = '"\')]}»>—–-'


def _ts(seconds: float) -> str:
    td = timedelta(seconds=max(0.0, seconds))
    total_ms = int(round(td.total_seconds() * 1000))
    hh, rem = divmod(total_ms, 3600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def write_per_word_srt(words: list[Word], out: Path) -> None:
    """One cue per word — debug aid for verifying alignment."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for i, w in enumerate(words, 1):
            f.write(f"{i}\n{_ts(w.start)} --> {_ts(w.end)}\n{w.text.strip()}\n\n")


def _ends_sentence(word_text: str) -> bool:
    """True if `word_text` ends a sentence (with abbreviation guard for periods)."""
    if not word_text:
        return False
    stripped = word_text.rstrip(TRAILING_PUNCT).strip()
    if not stripped:
        return False
    last = stripped[-1]
    if last not in SENTENCE_TERMINATORS:
        return False
    if last == ".":
        # Abbreviation guard: "Mr.", "Dr.", "U.S.", "p.m." — short core, period.
        # `!` and `?` are unambiguous (no abbreviations use them).
        no_punct = stripped.rstrip(".").strip()
        if len(no_punct) <= 2:
            return False
    return True


def _build_emit_plan(words: list[Word], decisions: dict[int, str | None]):
    """Walk words and yield (start_idx, last_idx_covered, display_text).

    For non-phrase words: start_idx == last_idx == i.
    For phrase replacements: start_idx is the first word, last_idx is the last
    word of the phrase span (so sentence-end checks see the phrase's true tail).
    Words with display=None (continuation of an emitted phrase) are skipped here.
    """
    n = len(words)
    i = 0
    while i < n:
        if i in decisions:
            display = decisions[i]
            if display is None:
                i += 1
                continue
            j = i + 1
            while j < n and j in decisions and decisions[j] is None:
                j += 1
            yield (i, j - 1, display)
            i = j
        else:
            display = words[i].text.strip()
            if display:
                yield (i, i, display)
            i += 1


def _build_cues(
    words: list[Word],
    decisions: dict[int, str | None],
    max_duration: float,
    max_pause: float,
    tail_seconds: float,
) -> list[tuple[float, float, list[str]]]:
    """Return list of (start_seconds, end_seconds, [display_strings])."""
    emits = list(_build_emit_plan(words, decisions))
    if not emits:
        return []

    # Minimum cue length before we honor a sentence-end break (avoid 0.2s flashes).
    MIN_CUE_BEFORE_SENTENCE_BREAK = 0.8

    cues: list[tuple[float, float, list[str]]] = []
    cur_start: float | None = None
    cur_displays: list[str] = []
    cur_last_word_end = 0.0

    for k, (start_idx, last_idx, display) in enumerate(emits):
        if cur_start is None:
            cur_start = words[start_idx].start
        cur_displays.append(display)
        cur_last_word_end = words[last_idx].end

        is_last_emit = k == len(emits) - 1
        next_emit_start_idx = emits[k + 1][0] if not is_last_emit else None
        next_word_start = (
            words[next_emit_start_idx].start if next_emit_start_idx is not None else None
        )

        should_break = is_last_emit
        # Break on long inter-word silence
        if (
            next_word_start is not None
            and (next_word_start - cur_last_word_end) >= max_pause
        ):
            should_break = True
        # Break on sentence end (using ORIGINAL whisper text of the last word covered)
        if (
            _ends_sentence(words[last_idx].text)
            and (cur_last_word_end - cur_start) >= MIN_CUE_BEFORE_SENTENCE_BREAK
        ):
            should_break = True
        # Hard cap on cue duration for readability
        if (cur_last_word_end - cur_start) >= max_duration:
            should_break = True

        if should_break:
            display_end = cur_last_word_end + tail_seconds
            if next_word_start is not None:
                # Don't crowd the next cue — leave at least 50 ms of breathing room.
                display_end = min(display_end, next_word_start - 0.05)
            display_end = max(display_end, cur_last_word_end + 0.01)
            cues.append((cur_start, display_end, cur_displays))
            cur_start = None
            cur_displays = []
            cur_last_word_end = 0.0

    return cues


def write_cleaned_srt(
    words: list[Word],
    out: Path,
    swears: set[str],
    phrases: PhraseSet | None,
    word_replacements: dict[str, str],
    phrase_replacements: dict[str, str],
    word_default: str,
    phrase_default: str,
    segment_max_duration: float = 5.0,
    max_pause_seconds: float = 1.5,
    tail_seconds: float = 0.5,
) -> None:
    """Write a Kodi/standard .srt with profanity replaced and readable cue boundaries."""
    out.parent.mkdir(parents=True, exist_ok=True)
    hits = detect(words, swears, phrases)

    decisions: dict[int, str | None] = {}
    for hit in hits:
        s, e = hit.span
        if hit.is_phrase:
            decisions[s] = replacement_for(hit.matched, phrase_replacements, phrase_default)
            for k in range(s + 1, e):
                decisions[k] = None
        else:
            decisions[s] = replacement_for(hit.matched, word_replacements, word_default)

    cues = _build_cues(
        words=words,
        decisions=decisions,
        max_duration=segment_max_duration,
        max_pause=max_pause_seconds,
        tail_seconds=tail_seconds,
    )

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for i, (start, end, ws) in enumerate(cues, 1):
            f.write(f"{i}\n{_ts(start)} --> {_ts(end)}\n{' '.join(ws).strip()}\n\n")
