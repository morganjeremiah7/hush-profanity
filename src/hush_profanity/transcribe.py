"""Whisper transcription with WhisperX wav2vec2 forced alignment.

Pipeline:
  1. faster-whisper transcribes the audio (fast, accurate text).
  2. WhisperX wav2vec2 alignment refines word boundaries to ~20 ms precision.

We need tight word boundaries because muting a swear 200 ms late lets the consonant
through. Whisper's own word timestamps are cross-attention guesses and routinely drift.

The Transcriber class loads the Whisper model and the alignment model ONCE and reuses
them across many files. The previous version reloaded both per file (~10 s overhead),
which became the dominant cost for large batches once the parallel pipeline removed
encode and post-processing latency.
"""
from __future__ import annotations

import gc
import logging
import os
import site
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import AlignmentCfg, WhisperCfg

log = logging.getLogger(__name__)


def _ensure_cuda_dlls_on_path() -> None:
    """Make the bundled nvidia-* DLLs discoverable by ctranslate2 on Windows.

    PyTorch bundles its own cuDNN inside torch\\lib and ctypes-loads it directly,
    so torch is fine. ctranslate2 (the faster-whisper backend) calls plain
    LoadLibrary and relies on the OS DLL search order. We install the right DLLs
    via `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (8.9.7.29) — they land in
    .venv\\Lib\\site-packages\\nvidia\\<pkg>\\bin. To be findable by both
    AddDllDirectory-aware loaders AND legacy LoadLibrary, we do both:
      1. os.add_dll_directory(d)          — for LOAD_LIBRARY_SEARCH_USER_DIRS
      2. prepend d to os.environ['PATH']  — for legacy LoadLibrary (ctranslate2)
    """
    if sys.platform != "win32":
        return
    candidates: list[str] = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        nvidia_root = Path(sp) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for sub in nvidia_root.iterdir():
            bin_dir = sub / "bin"
            if bin_dir.is_dir():
                candidates.append(str(bin_dir))
    if not candidates:
        return
    for d in candidates:
        try:
            os.add_dll_directory(d)
        except (OSError, FileNotFoundError):
            pass
    existing = os.environ.get("PATH", "")
    new_path = os.pathsep.join(candidates + [existing]) if existing else os.pathsep.join(candidates)
    os.environ["PATH"] = new_path


_ensure_cuda_dlls_on_path()


@dataclass
class Word:
    text: str
    start: float
    end: float
    score: float = 1.0


class Transcriber:
    """Loads Whisper + alignment models once; transcribes many files.

    Not thread-safe — only ONE thread should call .transcribe() at a time. The
    parallel pipeline owns a single Transcriber that lives on the GPU worker thread.
    """

    def __init__(self, whisper_cfg: WhisperCfg, alignment_cfg: AlignmentCfg, batch_size: int = 1) -> None:
        self.whisper_cfg = whisper_cfg
        self.alignment_cfg = alignment_cfg
        self.batch_size = max(1, int(batch_size))
        self._model = None
        self._batched = None
        self._align_model = None
        self._align_metadata = None
        self._align_lang: str | None = None

    def load(self) -> None:
        """Load models if not already loaded. Idempotent."""
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        log.info("Loading Whisper model %s on %s (%s)",
                 self.whisper_cfg.model, self.whisper_cfg.device, self.whisper_cfg.compute_type)
        self._model = WhisperModel(
            self.whisper_cfg.model,
            device=self.whisper_cfg.device,
            device_index=self.whisper_cfg.device_index,
            compute_type=self.whisper_cfg.compute_type,
        )

        if self.batch_size > 1:
            from faster_whisper import BatchedInferencePipeline
            log.info("Wrapping in BatchedInferencePipeline (batch_size=%d)", self.batch_size)
            self._batched = BatchedInferencePipeline(model=self._model)

        if self.alignment_cfg.enabled:
            try:
                import whisperx
                lang = self.whisper_cfg.language or "en"
                log.info("Loading wav2vec2 alignment model (language=%s)", lang)
                self._align_model, self._align_metadata = whisperx.load_align_model(
                    language_code=lang, device=self.whisper_cfg.device,
                )
                self._align_lang = lang
            except Exception as e:
                log.warning("Alignment model load failed (%s) — alignment will be skipped", e)
                self._align_model = None

    def transcribe(self, audio_wav: Path) -> list[Word]:
        """Transcribe one file, return per-word records with precise timestamps."""
        self.load()

        if self._batched is not None:
            log.debug("Transcribing %s (batched, batch_size=%d)", audio_wav.name, self.batch_size)
            segments_iter, info = self._batched.transcribe(
                str(audio_wav),
                batch_size=self.batch_size,
                language=self.whisper_cfg.language,
                vad_filter=self.whisper_cfg.vad_filter,
                word_timestamps=True,
            )
        else:
            log.debug("Transcribing %s (sequential)", audio_wav.name)
            segments_iter, info = self._model.transcribe(
                str(audio_wav),
                language=self.whisper_cfg.language,
                beam_size=self.whisper_cfg.beam_size,
                vad_filter=self.whisper_cfg.vad_filter,
                word_timestamps=True,
                condition_on_previous_text=False,  # sequential mode: avoid hallucination loops
            )

        raw_segments = []
        for seg in segments_iter:
            words = []
            if seg.words:
                for w in seg.words:
                    words.append({
                        "word": w.word,
                        "start": float(w.start) if w.start is not None else float(seg.start),
                        "end": float(w.end) if w.end is not None else float(seg.end),
                        "probability": float(w.probability) if w.probability is not None else 1.0,
                    })
            raw_segments.append({
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "words": words,
            })

        if self._align_model is None:
            return _flatten_words(raw_segments)

        try:
            import whisperx
            aligned = whisperx.align(
                raw_segments,
                self._align_model,
                self._align_metadata,
                str(audio_wav),
                self.whisper_cfg.device,
                return_char_alignments=False,
            )
            return _flatten_words(aligned.get("segments", raw_segments))
        except Exception as e:
            log.warning("Alignment failed for %s (%s) — falling back to Whisper word timestamps",
                        audio_wav.name, e)
            return _flatten_words(raw_segments)

    def close(self) -> None:
        """Free GPU memory. Optional — called automatically on process exit."""
        self._model = None
        self._batched = None
        self._align_model = None
        self._align_metadata = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def transcribe_to_words(audio_wav: Path, whisper_cfg: WhisperCfg,
                        alignment_cfg: AlignmentCfg, batch_size: int = 1) -> list[Word]:
    """Single-shot helper — for one-off use. Reloads models every call (slow).

    Prefer instantiating Transcriber once and reusing it for batches.
    """
    t = Transcriber(whisper_cfg, alignment_cfg, batch_size=batch_size)
    try:
        return t.transcribe(audio_wav)
    finally:
        t.close()


def _flatten_words(segments: list[dict]) -> list[Word]:
    out: list[Word] = []
    for seg in segments:
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            start = w.get("start")
            end = w.get("end")
            if not text or start is None or end is None:
                continue
            out.append(Word(
                text=text,
                start=float(start),
                end=float(end),
                score=float(w.get("score", w.get("probability", 1.0))),
            ))
    return out
