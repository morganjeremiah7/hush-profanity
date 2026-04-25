"""Library walker + per-file processing pipeline + crash-safe checkpointing.

For each video:
    1. Probe duration + select audio track.
    2. Extract a 16 kHz mono WAV to a scratch file.
    3. Transcribe + align (transcribe.py).
    4. Detect profanity (profanity.py) and build EDL entries (edl.py).
    5. Read existing .edl, replace ONLY the auto section, keep manual entries, write back.
    6. (Optional) Correct against official .srt and write cleaned .srt.
    7. (Optional) Write per-word debug .srt.
    8. Mark file as processed in the checkpoint file.

Anything that goes wrong on one file is logged and the loop continues.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import audio, correct, edl, profanity, srt, transcribe
from .config import Settings, load_replacements, load_swear_words

log = logging.getLogger(__name__)


@dataclass
class FileResult:
    path: Path
    ok: bool
    profanity_count: int = 0
    duration_seconds: float | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None


def find_videos(roots: list[Path], extensions: list[str]) -> list[Path]:
    exts = {e.lower() for e in extensions}
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            log.warning("Library root does not exist: %s", root)
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                found.append(p)
    return sorted(found)


def _load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("done", []))
    except Exception as e:
        log.warning("Could not read checkpoint %s: %s — starting fresh", path, e)
        return set()


def _save_checkpoint(path: Path, done: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, indent=2)
    tmp.replace(path)


def process_one(video: Path, settings: Settings, swears: set[str],
                replacements: dict[str, str], default_repl: str) -> FileResult:
    """Process a single video. Never raises — errors are returned in FileResult."""
    started = time.monotonic()
    edl_path = video.with_suffix(".edl")
    srt_path = video.with_suffix(".srt")
    words_srt_path = video.with_name(f"{video.stem}-words.srt")

    # Skip if we've already produced auto entries for this file (keeps re-runs cheap).
    if settings.library.skip_if_processed and edl_path.exists():
        existing = edl.EdlFile.read(edl_path, title=video.stem)
        if existing.has_auto_entries():
            log.info("Skipping (already has auto EDL): %s", video.name)
            return FileResult(path=video, ok=True, profanity_count=len(existing.auto),
                              elapsed_seconds=time.monotonic() - started)

    duration = audio.probe_duration(video)
    audio_idx = audio.select_audio_track(video, prefer_language=settings.whisper.audio_language)
    log.info("Processing %s (duration=%.1fs, audio_track=%d)",
             video.name, duration or -1, audio_idx)

    with tempfile.TemporaryDirectory(prefix="hush-") as scratch:
        wav = Path(scratch) / "audio.wav"
        try:
            audio.extract_wav(video, wav, audio_index=audio_idx)
        except audio.AudioExtractError as e:
            log.error("Audio extraction failed for %s: %s", video, e)
            return FileResult(path=video, ok=False, duration_seconds=duration,
                              elapsed_seconds=time.monotonic() - started, error=str(e))

        try:
            words = transcribe.transcribe_to_words(wav, settings.whisper, settings.alignment)
        except Exception as e:
            log.exception("Transcription failed for %s", video)
            return FileResult(path=video, ok=False, duration_seconds=duration,
                              elapsed_seconds=time.monotonic() - started, error=f"transcribe: {e}")

    if not words:
        log.warning("No words returned for %s — writing empty EDL", video.name)

    # Profanity is always detected from raw Whisper output (official subs may be censored).
    hits = profanity.detect(words, swears)
    log.info("Found %d profanity hit(s) in %s", len(hits), video.name)

    auto_entries = edl.entries_from_profanity_hits(
        hits,
        padding=settings.edl.padding_seconds,
        action=settings.edl.profanity_action,
        merge_gap=settings.edl.merge_gap_seconds,
    )

    # Preserve any manual section in the existing .edl.
    edl_file = edl.EdlFile.read(edl_path, title=video.stem)
    edl_file.auto = auto_entries
    edl_file.title = video.stem
    edl_file.write(edl_path)

    # Subtitles: optionally correct against official .srt before writing.
    display_words = words
    if settings.subtitles.use_official_subs:
        official_path = correct.find_official_subtitle(video, settings.subtitles.official_sub_suffixes)
        if official_path:
            try:
                official_words = correct.parse_srt_words(official_path)
                display_words = correct.correct_words(words, official_words)
                log.info("Corrected against official subtitle: %s", official_path.name)
            except Exception as e:
                log.warning("Subtitle correction failed for %s (%s) — using raw Whisper", video.name, e)

    if settings.subtitles.generate_srt:
        srt.write_cleaned_srt(
            display_words, srt_path, swears, replacements, default_repl,
            segment_max_duration=settings.subtitles.segment_max_duration,
        )
    if settings.subtitles.generate_words_srt:
        srt.write_per_word_srt(words, words_srt_path)

    return FileResult(
        path=video,
        ok=True,
        profanity_count=len(hits),
        duration_seconds=duration,
        elapsed_seconds=time.monotonic() - started,
    )


def run(settings: Settings) -> list[FileResult]:
    swears = load_swear_words(settings.paths.swears_file)
    replacements, default_repl = load_replacements(settings.paths.replacements_file)
    log.info("Loaded %d swear words and %d replacement entries",
             len(swears), len(replacements))

    videos = find_videos(settings.library.roots, settings.library.extensions)
    log.info("Found %d candidate video file(s) across %d root(s)",
             len(videos), len(settings.library.roots))

    done = _load_checkpoint(settings.paths.checkpoint_file)
    results: list[FileResult] = []
    try:
        for i, video in enumerate(videos, 1):
            key = str(video)
            if key in done:
                continue
            log.info("[%d/%d] %s", i, len(videos), video)
            result = process_one(video, settings, swears, replacements, default_repl)
            results.append(result)
            if result.ok:
                done.add(key)
                _save_checkpoint(settings.paths.checkpoint_file, done)
            else:
                log.error("FAILED: %s — %s", video, result.error)
    except KeyboardInterrupt:
        log.warning("Interrupted by user — checkpoint saved, safe to resume")
    return results
