"""Library walker + 3-stage parallel pipeline + crash-safe checkpointing.

Pipeline:

    encode_q  ->  [N encode workers]  ->  gpu_q  ->  [K GPU workers]  ->  post_q  ->  [M post workers]

    encode workers (CPU): ffmpeg-extract WAV from each video into a private tempdir.
    GPU workers (CPU):    each spawns a fresh python subprocess per file that
                          does the actual GPU work (Whisper transcribe + wav2vec2
                          alignment) and returns word JSON. Subprocess isolation
                          is the workaround for ctranslate2's CUDA cleanup bug
                          (OpenNMT/CTranslate2#1912) which corrupted the heap
                          after 1-3 in-process model destructions on Windows.
                          We've since switched to openai-whisper (no ctranslate2),
                          but kept subprocess isolation as belt-and-suspenders —
                          a CUDA hiccup on one file can't poison the rest of the
                          run. K = settings.performance.gpu_workers (typically 1
                          on 12-23 GB cards, 2 on 24 GB+).
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
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

from . import audio, edl, profanity, srt
from .config import Settings, load_phrase_lines, load_replacements, load_swear_words

log = logging.getLogger(__name__)


# Hard ceiling so a wedged subprocess can't block the pipeline forever.
# With gpu_workers=2 on a 3090, long films (90-120 min) routinely take 25-30
# min wall time per file due to GPU contention; the previous 30-min cap was
# skipping ~half the long-form library. 60 min covers anything up to ~2 hr of
# audio with the current dual-worker pace and still bounds runaway workers.
SUBPROCESS_TIMEOUT_SECONDS = 60 * 60


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
    # 1-based index into the producer's todo list, plus total length, so each
    # log line can show "[27/7605]" for at-a-glance progress.
    index: int = 0
    total: int = 0
    # True if this video was already in the checkpoint when the run started.
    # Used by the SRT preservation logic to decide whether an existing
    # <base>.srt is ours (safe to overwrite) or the user's (rename first).
    was_processed: bool = False


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


def _run_subprocess_transcribe(wav_path: Path, settings: Settings):
    """Spawn a fresh python subprocess to transcribe one WAV; return Word list.

    The subprocess loads its own WhisperModel + alignment model, transcribes,
    writes word JSON to a temp file, and exits. Process exit lets the OS clean
    up the CUDA context fully — sidestepping the in-process cleanup bug that
    crashes ctranslate2 after 1-3 model destructions in the same Python.

    Raises subprocess.TimeoutExpired if the subprocess hangs past the ceiling.
    Raises RuntimeError if it exits non-zero (caller logs + skips the file).
    """
    # transcribe.Word is the canonical type the rest of the pipeline expects;
    # import here to avoid circulars at module load.
    from .transcribe import Word

    cfg = {
        "wav_path": str(wav_path),
        "whisper": asdict(settings.whisper),
        "alignment": asdict(settings.alignment),
        "batch_size": settings.performance.whisper_batch_size,
    }
    with tempfile.TemporaryDirectory(prefix="hush-ipc-") as ipc_dir:
        ipc = Path(ipc_dir)
        cfg_path = ipc / "config.json"
        out_path = ipc / "words.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

        # sys.executable is the venv python that imported this module — same
        # interpreter, same packages, no PATH ambiguity.
        cmd = [
            sys.executable,
            "-m", "hush_profanity._transcribe_worker",
            str(cfg_path),
            str(out_path),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        # Surface worker stderr (info + warnings) into the parent log.
        if proc.stderr:
            for line in proc.stderr.rstrip().splitlines():
                log.info("  %s", line)
        if proc.returncode != 0:
            raise RuntimeError(
                f"transcribe worker exited {proc.returncode} for {wav_path.name}; "
                f"see worker stderr above for details"
            )
        if not out_path.exists():
            raise RuntimeError(
                f"transcribe worker exited 0 but produced no output file for {wav_path.name}"
            )
        records = json.loads(out_path.read_text(encoding="utf-8"))
    return [Word(**r) for r in records]


def _write_outputs(item: _WorkItem, settings: Settings, ctx: DetectionContext) -> int:
    """Detect profanity, write EDL + SRT for one item. Returns the profanity hit count.

    Raises on write failure — caller catches and records the error.
    """
    video = item.video
    words = item.words or []
    edl_path = video.with_suffix(".edl")
    srt_path = video.with_suffix(".srt")
    original_srt_path = video.with_suffix(".original.srt")
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
        # Preserve any user-supplied .srt the first time we touch this video.
        # The .original.srt sentinel makes this idempotent: once we've moved a
        # user srt aside, subsequent runs see the sentinel and overwrite our
        # own output freely. The was_processed check covers a second edge:
        # if the user deletes .original.srt but the checkpoint still has the
        # video, the existing .srt is ours, not theirs.
        if (srt_path.exists()
                and not original_srt_path.exists()
                and not item.was_processed):
            try:
                srt_path.rename(original_srt_path)
                log.info("Preserved existing subtitles: %s -> %s",
                         srt_path.name, original_srt_path.name)
            except Exception as e:
                log.warning("Could not preserve existing %s (%s) — overwriting",
                            srt_path.name, e)
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
                   done: set[str]):
    """Build worker callables + queues. Caller starts the threads.

    No persistent transcriber: each file gets a fresh WhisperModel +
    alignment model loaded inside the GPU worker, then torn down before the
    next file. Costs ~10 s per file in load overhead but eliminates the
    long-running-state wedge we hit with persistent models (see commit
    history if curious).
    """
    perf = settings.performance
    encode_q: Queue = Queue(maxsize=perf.encode_workers + 1)
    # gpu_q must hold at least one item per GPU worker (so workers don't starve)
    # plus a small lookahead so the encoders don't block.
    gpu_q: Queue = Queue(maxsize=max(2, perf.gpu_workers + 1))
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
            tag = f"[{item.index}/{item.total}]"

            edl_path = video.with_suffix(".edl")
            if settings.library.skip_if_processed and edl_path.exists():
                existing = edl.EdlFile.read(edl_path, title=video.stem)
                if existing.has_auto_entries():
                    log.info("%s SKIP: %s (already has auto EDL)", tag, video)
                    item.skipped = True
                    gpu_q.put(item)
                    continue

            log.info("%s BEGIN: %s", tag, video)
            try:
                item.tempdir = Path(tempfile.mkdtemp(prefix="hush-"))
                item.wav_path = item.tempdir / "audio.wav"
                item.audio_idx = audio.select_audio_track(
                    video, prefer_language=settings.whisper.audio_language
                )
                item.duration = audio.probe_duration(video)
                log.info("%s [encode] %s (duration=%.1fs, audio_track=%d)",
                         tag, video.name, item.duration or -1, item.audio_idx)
                audio.extract_wav(video, item.wav_path, item.audio_idx)
            except Exception as e:
                log.exception("%s [encode] failed: %s", tag, video)
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
                log.info("[gpu] transcribing %s (subprocess)", item.video.name)
                t0 = time.monotonic()
                item.words = _run_subprocess_transcribe(
                    wav_path=item.wav_path,
                    settings=settings,
                )
                log.info("[gpu] %d words from %s in %.1fs",
                         len(item.words), item.video.name, time.monotonic() - t0)
            except subprocess.TimeoutExpired:
                log.error("[gpu] timed out (>%ds) on %s — skipping",
                          SUBPROCESS_TIMEOUT_SECONDS, item.video)
                item.error = f"transcribe-timeout: subprocess exceeded {SUBPROCESS_TIMEOUT_SECONDS}s"
            except Exception as e:
                log.exception("[gpu] failed: %s", item.video)
                item.error = f"transcribe: {e}"
            post_q.put(item)

    def post_worker() -> None:
        while True:
            item = post_q.get()
            if item is None:
                return
            tag = f"[{item.index}/{item.total}]"
            try:
                if item.skipped:
                    elapsed = time.monotonic() - item.started
                    _record(FileResult(path=item.video, ok=True,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    continue
                if item.error:
                    elapsed = time.monotonic() - item.started
                    log.error("%s FAIL: %s (%s) wall=%.1fs — will retry on next run",
                              tag, item.video, item.error, elapsed)
                    _record(FileResult(path=item.video, ok=False, error=item.error,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    continue
                try:
                    hit_count = _write_outputs(item, settings, ctx)
                    elapsed = time.monotonic() - item.started
                    log.info("%s DONE: %s (wall=%.1fs, %d hits)",
                             tag, item.video, elapsed, hit_count)
                    _record(FileResult(path=item.video, ok=True,
                                       profanity_count=hit_count,
                                       duration_seconds=item.duration,
                                       elapsed_seconds=elapsed))
                    with checkpoint_lock:
                        done.add(str(item.video))
                        _save_checkpoint(settings.paths.checkpoint_file, done)
                except Exception as e:
                    log.exception("%s [post] failed: %s", tag, item.video)
                    elapsed = time.monotonic() - item.started
                    log.error("%s FAIL: %s (post: %s) wall=%.1fs — will retry on next run",
                              tag, item.video, e, elapsed)
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

    encode_q, gpu_q, post_q, encode_worker, gpu_worker, post_worker, results = (
        _make_pipeline(settings, ctx, todo, done)
    )

    encode_threads = [
        threading.Thread(target=encode_worker, name=f"encode-{i}", daemon=True)
        for i in range(perf.encode_workers)
    ]
    gpu_threads = [
        threading.Thread(target=gpu_worker, name=f"gpu-{i}", daemon=True)
        for i in range(perf.gpu_workers)
    ]
    post_threads = [
        threading.Thread(target=post_worker, name=f"post-{i}", daemon=True)
        for i in range(perf.post_workers)
    ]

    for t in encode_threads:
        t.start()
    for t in gpu_threads:
        t.start()
    for t in post_threads:
        t.start()

    interrupted = False
    total = len(todo)
    try:
        for i, video in enumerate(todo, start=1):
            encode_q.put(_WorkItem(
                video=video,
                index=i,
                total=total,
                was_processed=str(video) in done,
            ))
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted by user — draining in-flight files (Ctrl+C again to force-quit)")

    # Sentinel chain: producer is done; signal each stage in order.
    for _ in range(perf.encode_workers):
        encode_q.put(None)
    for t in encode_threads:
        t.join()
    for _ in range(perf.gpu_workers):
        gpu_q.put(None)
    for t in gpu_threads:
        t.join()
    for _ in range(perf.post_workers):
        post_q.put(None)
    for t in post_threads:
        t.join()

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

    log.info("Pipeline: %d encode workers, %d GPU worker(s), %d post workers",
             settings.performance.encode_workers,
             settings.performance.gpu_workers,
             settings.performance.post_workers)

    return _run_pipeline(settings, ctx, todo, done)
