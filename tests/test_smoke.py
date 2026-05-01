"""Smoke tests for hush-profanity. Run with `pytest tests/`.

These verify the package is importable and basic detection works. They do NOT
exercise CUDA / Whisper — that requires a GPU and a real audio file. For end-
to-end testing, run `windows\\scan.bat` against a folder with one short clip.
"""
from __future__ import annotations


def test_all_modules_import() -> None:
    """Every public module in the package imports cleanly."""
    import importlib

    for name in (
        "audio",
        "cli",
        "clean",
        "config",
        "edl",
        "profanity",
        "scanner",
        "srt",
        "transcribe",
        "_transcribe_worker",
        "webui.server",
    ):
        importlib.import_module(f"hush_profanity.{name}")


def test_profanity_detects_simple_word() -> None:
    """The single-word matcher catches a known swear in a one-word transcript."""
    from hush_profanity import profanity
    from hush_profanity.transcribe import Word

    words = [Word(text="shit", start=0.0, end=0.5, score=1.0)]
    swears = {"shit"}
    phrases = profanity.compile_phrases([])

    hits = profanity.detect(words, swears, phrases)

    assert len(hits) == 1
    assert hits[0].matched == "shit"


def test_profanity_handles_suffix_variants() -> None:
    """Suffix-stripping catches "fucking" → "fuck"."""
    from hush_profanity import profanity
    from hush_profanity.transcribe import Word

    words = [Word(text="fucking", start=0.0, end=0.5, score=1.0)]
    swears = {"fuck"}
    phrases = profanity.compile_phrases([])

    hits = profanity.detect(words, swears, phrases)

    assert len(hits) == 1


def test_clean_word_passes_through() -> None:
    """A non-swear word produces no hits."""
    from hush_profanity import profanity
    from hush_profanity.transcribe import Word

    words = [Word(text="hello", start=0.0, end=0.5, score=1.0)]
    swears = {"shit", "fuck"}
    phrases = profanity.compile_phrases([])

    hits = profanity.detect(words, swears, phrases)

    assert len(hits) == 0
