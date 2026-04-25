"""Whisper transcription with WhisperX wav2vec2 forced alignment.

Pipeline:
  1. faster-whisper transcribes the audio (fast, accurate text).
  2. WhisperX wav2vec2 alignment refines word boundaries to ~20 ms precision.

We need tight word boundaries because muting a swear 200 ms late lets the consonant
through. Whisper's own word timestamps are cross-attention guesses and routinely drift.
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
    """Add nvidia-* DLL bin directories to the Windows DLL loader path.

    PyTorch bundles its own cuDNN inside torch\\lib and uses ctypes to load it
    directly, so torch works even when the loader can't see those DLLs. ctranslate2
    (used by faster-whisper) just calls LoadLibrary and relies on the OS — so it
    needs the DLLs on the path. We install them via `nvidia-cublas-cu12` and
    `nvidia-cudnn-cu12` pip packages, which land them under
    .venv\\Lib\\site-packages\\nvidia\\<pkg>\\bin. This makes them findable.
    """
    if sys.platform != "win32":
        return
    candidates: list[Path] = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        nvidia_root = Path(sp) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for sub in nvidia_root.iterdir():
            bin_dir = sub / "bin"
            if bin_dir.is_dir():
                candidates.append(bin_dir)
    for d in candidates:
        try:
            os.add_dll_directory(str(d))
        except (OSError, FileNotFoundError):
            pass


_ensure_cuda_dlls_on_path()


@dataclass
class Word:
    text: str
    start: float
    end: float
    score: float = 1.0


def transcribe_to_words(
    audio_wav: Path,
    whisper_cfg: WhisperCfg,
    alignment_cfg: AlignmentCfg,
) -> list[Word]:
    """Return per-word records with precise timestamps."""
    import torch  # imported lazily so config-only operations don't pay the import cost
    from faster_whisper import WhisperModel

    log.info("Loading Whisper model %s on %s (%s)",
             whisper_cfg.model, whisper_cfg.device, whisper_cfg.compute_type)
    model = WhisperModel(
        whisper_cfg.model,
        device=whisper_cfg.device,
        device_index=whisper_cfg.device_index,
        compute_type=whisper_cfg.compute_type,
    )

    log.info("Transcribing %s", audio_wav.name)
    segments_iter, info = model.transcribe(
        str(audio_wav),
        language=whisper_cfg.language,
        beam_size=whisper_cfg.beam_size,
        vad_filter=whisper_cfg.vad_filter,
        word_timestamps=True,
        condition_on_previous_text=False,  # reduces hallucination loops
    )
    log.info("Detected language: %s (probability %.2f)", info.language, info.language_probability)

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

    # Free the Whisper model before loading the alignment model — they fight for VRAM.
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    if not alignment_cfg.enabled:
        return _flatten_words(raw_segments)

    log.info("Loading wav2vec2 alignment model")
    try:
        import whisperx
        align_model, metadata = whisperx.load_align_model(
            language_code=info.language,
            device=whisper_cfg.device,
        )
        aligned = whisperx.align(
            raw_segments,
            align_model,
            metadata,
            str(audio_wav),
            whisper_cfg.device,
            return_char_alignments=False,
        )
        del align_model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return _flatten_words(aligned.get("segments", raw_segments))
    except Exception as e:
        log.warning("Alignment failed (%s) — falling back to Whisper word timestamps", e)
        return _flatten_words(raw_segments)


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
