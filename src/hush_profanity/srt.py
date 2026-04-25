"""Subtitle (.srt) generation."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from .profanity import PhraseSet, detect, replacement_for
from .transcribe import Word


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
) -> None:
    """Group words into cues of up to `segment_max_duration` and replace any swears.

    Phrase matches consume multiple words and emit one replacement at the start
    of the span; subsequent words in the same span are skipped.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    hits = detect(words, swears, phrases)

    # Build a per-word-index decision map: index -> str (replacement) or None (skip)
    decision: dict[int, str | None] = {}
    for hit in hits:
        s, e = hit.span
        if hit.is_phrase:
            decision[s] = replacement_for(hit.matched, phrase_replacements, phrase_default)
            for k in range(s + 1, e):
                decision[k] = None
        else:
            decision[s] = replacement_for(hit.matched, word_replacements, word_default)

    cues: list[tuple[float, float, list[str]]] = []
    cue_start: float | None = None
    cue_words: list[str] = []
    cue_end = 0.0

    for i, w in enumerate(words):
        if i in decision:
            display = decision[i]
            if display is None:
                # Subsequent word in a phrase span — already covered.
                continue
        else:
            display = w.text.strip()
        if not display:
            continue
        if cue_start is None:
            cue_start = w.start
        cue_words.append(display)
        cue_end = w.end
        if cue_end - cue_start >= segment_max_duration:
            cues.append((cue_start, cue_end, cue_words))
            cue_start = None
            cue_words = []
    if cue_words:
        cues.append((cue_start or 0.0, cue_end, cue_words))

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for i, (start, end, ws) in enumerate(cues, 1):
            f.write(f"{i}\n{_ts(start)} --> {_ts(end)}\n{' '.join(ws).strip()}\n\n")
