"""Library walker + 3-stage parallel pipeline + crash-safe checkpointing.

Pipeline:

    encode_q  ->  [N encode workers]  ->  gpu_q  ->  [1 GPU worker]  ->  post_q  ->  [M post workers]

    encode workers (CPU): ffmpeg-extract WAV from each video into a private tempdir.
    GPU worker (GPU):     transcribe WAV with Whisper + wav2vec2 alignment.
    post workers (CPU):   detect profanity, write EDL + SRT, save checkpoint, delete tempdir.

All three stages run concurrently on different files. At steady state, while the GPU is
transcribing file N, encoders are already preparing file N+1 and N+2, and post workers
are writing the outputs for file N-1. The GPU is the throughput bottleneck — encode and
post never need to wait for it.

Bounded queues prevent runaway memory if one stage stalls. Sentinel propagation
(None values) shuts the pipeline down cleanly after the producer exhausts the work list.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

from . import audio, edl, profanity, srt, transcribe
from .config import Settings, load_phrase_lines, load_replacements, load_swear_words

log = logging.getLogger(__name__)


@dataclass
class FileResult:
    path: Path
    ok: bool
    profanity_count: int = 0
    duration_seconds: float | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None


@dataclass
class DetectionContext:
    swears: set[str]
    phrases: profanity.PhraseSet
    word_replacements: dict[str, str]
    phrase_replacements: dict[str, str]
    word_default: str
    phrase_default: str


@dataclass
class _WorkItem:
    """Carries one file through all three pipeline stages."""
    video: Path
    started: float = 0.0
    tempdir: Path | None = None
    wav_path: Path | None = None
    audio_idx: int = 0
    duration: float | None = None
    words: list[Any] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False


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


def _write_outputs(item: _WorkItem, settings: Settings, ctx: DetectionContext) -> int:
    """Detect profanity, write EDL + SRT for one item. Returns the profanity hit count.

    Raises on write failure — caller catches and records the error.
    """
    video = item.video
    words = item.words or []
    edl_path = video.with_suffix(".edl")
    srt_path = video.with_suffix(".srt")
    words_srt_path = video.with_name(f"{video.stem}-words.srt")

    if not words:
        log.warning("No words returned for %s — writing empty EDL", video.name)

    hits = profanity.detect(words, ctx.swears, ctx.phrases)
    log.info("Found %d profanity hit(s) in %s", len(hits), video.name)

    auto_entries = edl.entries_from_profanity_hits(
        hits,
        padding=settings.edl.padding_seconds,
        action=settings.edl.profanity_action,
        merge_gap=settings.edl.merge_gap_seconds,
    )

    edl_file = edl.EdlFile.read(edl_path, title=video.stem)
    edl_file.auto = auto_entries
    edl_file.title = video.stem
    edl_file.write(edl_path)

    if settings.subtitles.generate_srt:
        srt.write_cleaned_srt(
            words, srt_path,
            ctx.swears, ctx.phrases,
            ctx.word_replacements, ctx.phrase_replacements,
            ctx.word_default, ctx.phrase_default,
            segment_max_duration=settings.subtitles.segment_max_duration,
            max_pause_seconds=settings.subtitles.max_pause_seconds,
            tail_seconds=settings.subtitles.tail_seconds,
        )
    if settings.subtitles.generate_words_srt:
        srt.write_per_word_srt(words, words_srt_path)

    return len(hits)


def _make_pipeline(settings: Settings, ctx: DetectionContext, todo: list[Path],
                   done: set[str], transcriber: transcribe.Transcriber):
    """Build worker callables + queues. Caller starts the threads."""
    perf = settings.performance
    encode_q: Queue = Queue(maxsize=perf.encode_workers + 1)
    gpu_q: Queue = Queue(maxsize=2)
    post_q: Queue = Queue(maxsize=perf.post_workers + 2)

    results: list[FileResult] = []
    results_lock = threading.Lock()
    checkpoint_lock = threading.Lock()

    def _record(r: FileResult) -> None:
        with results_lock:
            results.append(r)

    def encode_worker() -> None:
        while True:
            item = encode_q.get()
            if item is None:
                return
            video = item.video
            item.started = time.monotonic()

            edl_path = video.with_suffix(".edl")
            if settings.library.skip_if_processed and edl_path.exists():
                existing = edl.EdlFile.read(edl_path, title=video.stem)
                if existing.has_auto_entries():
                    log.info("Skipping (already has auto EDL): %s", video.name)
                    item.skipped = True
                    gpu_q.put(item)
                    continue

            try:
                item.tempdir = Path(tempfile.mkdtemp(prefix="hush-"))
                item.wav_path = item.tempdir / "audio.wav"
                item.audio_idx = audio.select_audio_track(
                    video, prefer_language=settings.whisper.audio_language
                )
                item.duration = audio.probe_duration(video)
                log.info("[encode] %s (duration=%.1fs, audio_track=%d)",
                         video.name, item.duration or -1, item.audio_idx)
                audio.extract_wav(video, item.wav_path, item.audio_idx)
            except Exception as e:
                log.exception("[encode] failed: %s", video)
                item.error = f"encode: {e}"
            gpu_q.put(item)

    def gpu_worker() -> None:
        while True:
            item = gpu_q.get()
            if item is None:
                return
            if item.skipped or item.error:
                post_q.put(item)
                continue
            try:
                log.info("[gpu] transcribing %s", item.video.name)
                t0 = time.monotonic()
                item.words = transcriber.transcribe(item.wav_path)
                log.info("[gpu] %d words from %s in %.1fs",
                         len(item.words), item.video.name, time.monotonic() - t0)
            except Exception as e:
                log.exception("[gpu] failed: %s", item.video)
                item.error = f"transcribe: {e}"
            post_q.put(item)

    def post_worker() -> None:
        while True:
            item = post_q.get()
            if item is None:
                return
            try:
                if item.skipped:
                    elapsed = time.monotonic() - item.started
                    _record(FileResult(path=item.video, ok=True,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    continue
                if item.error:
                    elapsed = time.monotonic() - item.started
                    _record(FileResult(path=item.video, ok=False, error=item.error,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    continue
                try:
                    hit_count = _write_outputs(item, settings, ctx)
                    elapsed = time.monotonic() - item.started
                    _record(FileResult(path=item.video, ok=True,
                                       profanity_count=hit_count,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    with checkpoint_lock:
                        done.add(str(item.video))
                        _save_checkpoint(settings.paths.checkpoint_file, done)
                except Exception as e:
                    log.exception("[post] failed: %s", item.video)
                    elapsed = time.monotonic() - item.started
                    _record(FileResult(path=item.video, ok=False, error=f"post: {e}",
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
            finally:
                if item.tempdir is not None:
                    shutil.rmtree(item.tempdir, ignore_errors=True)

    return encode_q, gpu_q, post_q, encode_worker, gpu_worker, post_worker, results


def _run_pipeline(settings: Settings, ctx: DetectionContext,
                  todo: list[Path], done: set[str]) -> list[FileResult]:
    perf = settings.performance
    transcriber = transcribe.Transcriber(
        settings.whisper, settings.alignment, batch_size=perf.whisper_batch_size,
    )

    encode_q, gpu_q, post_q, encode_worker, gpu_worker, post_worker, results = (
        _make_pipeline(settings, ctx, todo, done, transcriber)
    )

    encode_threads = [
        threading.Thread(target=encode_worker, name=f"encode-{i}", daemon=True)
        for i in range(perf.encode_workers)
    ]
    gpu_thread = threading.Thread(target=gpu_worker, name="gpu", daemon=True)
    post_threads = [
        threading.Thread(target=post_worker, name=f"post-{i}", daemon=True)
        for i in range(perf.post_workers)
    ]

    for t in encode_threads:
        t.start()
    gpu_thread.start()
    for t in post_threads:
        t.start()

    interrupted = False
    try:
        for video in todo:
            encode_q.put(_WorkItem(video=video))
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted by user — draining in-flight files (Ctrl+C again to force-quit)")

    # Sentinel chain: producer is done; signal each stage in order.
    for _ in range(perf.encode_workers):
        encode_q.put(None)
    for t in encode_threads:
        t.join()
    gpu_q.put(None)
    gpu_thread.join()
    for _ in range(perf.post_workers):
        post_q.put(None)
    for t in post_threads:
        t.join()

    transcriber.close()

    if interrupted:
        log.warning("Pipeline drained; checkpoint preserved — re-run to resume.")

    return results


def run(settings: Settings) -> list[FileResult]:
    swears = load_swear_words(settings.paths.swears_file)
    phrase_lines = load_phrase_lines(settings.paths.phrases_file)
    phrases = profanity.compile_phrases(phrase_lines)
    word_repl, phrase_repl, word_default, phrase_default = load_replacements(
        settings.paths.replacements_file
    )
    log.info("Loaded %d swear words, %d phrases, %d word replacements, %d phrase replacements",
             len(swears), len(phrase_lines), len(word_repl), len(phrase_repl))

    if not settings.library.roots:
        raise SystemExit(
            "No library roots configured. Edit config/settings.toml and set "
            "[library].roots to one or more folders to scan."
        )

    ctx = DetectionContext(
        swears=swears,
        phrases=phrases,
        word_replacements=word_repl,
        phrase_replacements=phrase_repl,
        word_default=word_default,
        phrase_default=phrase_default,
    )

    videos = find_videos(settings.library.roots, settings.library.extensions)
    log.info("Found %d candidate video file(s) across %d root(s)",
             len(videos), len(settings.library.roots))

    done = _load_checkpoint(settings.paths.checkpoint_file)
    todo = [v for v in videos if str(v) not in done]
    if not todo:
        log.info("Nothing to do — all videos already in checkpoint.")
        return []

    log.info("Pipeline: %d encode workers, 1 GPU worker (batch_size=%d), %d post workers",
             settings.performance.encode_workers,
             settings.performance.whisper_batch_size,
             settings.performance.post_workers)

    return _run_pipeline(settings, ctx, todo, done)
