"""Whisper transcription using openai-whisper (the reference PyTorch impl) +
WhisperX wav2vec2 forced alignment.

Why not faster-whisper / ctranslate2?
------------------------------------
ctranslate2 (the C++ inference engine that powers faster-whisper) has a
long-standing CUDA cleanup bug on Windows that destroys the heap when models
are torn down (OpenNMT/CTranslate2#1912, faster-whisper#71/#1293). We hit it
across every version we tried (4.4.0, 4.7.1) and across every workaround
(int8, no alignment, subprocess isolation). The bug is in the engine itself.

openai-whisper is the reference PyTorch implementation. ~3-4× slower than
faster-whisper's batched mode but uses only PyTorch's CUDA stack — same one
WhisperX uses for alignment — so there's only ONE CUDA library in the process.
No more dual-allocator instability. Tested through the cleanup bug fingerprint.

Trade-off accepted by the user: speed for stability.
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
    """Belt-and-suspenders: add any nvidia-* pip-installed DLL bin dirs to the
    Windows DLL loader path. With openai-whisper this is mostly redundant —
    PyTorch ships its own cuDNN inside torch\\lib and ctypes-loads it directly.
    We keep the helper for parity with old envs that may still have nvidia-*
    packages lying around.
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
    """Loads openai-whisper + alignment models once; transcribes many files.

    Not thread-safe — only ONE thread should call .transcribe() at a time.
    """

    def __init__(self, whisper_cfg: WhisperCfg, alignment_cfg: AlignmentCfg, batch_size: int = 1) -> None:
        # batch_size is kept on the signature for API compatibility with the
        # earlier faster-whisper Transcriber. openai-whisper has no equivalent
        # batching knob (it processes the whole file in one .transcribe() call).
        self.whisper_cfg = whisper_cfg
        self.alignment_cfg = alignment_cfg
        self.batch_size = max(1, int(batch_size))
        self._model = None
        self._align_model = None
        self._align_metadata = None

    def load(self) -> None:
        if self._model is not None:
            return

        import whisper  # openai-whisper

        log.info("Loading openai-whisper model %s on %s",
                 self.whisper_cfg.model, self.whisper_cfg.device)
        self._model = whisper.load_model(
            self.whisper_cfg.model,
            device=self.whisper_cfg.device,
        )

        if self.alignment_cfg.enabled:
            try:
                import whisperx
                lang = self.whisper_cfg.language or "en"
                log.info("Loading wav2vec2 alignment model (language=%s)", lang)
                self._align_model, self._align_metadata = whisperx.load_align_model(
                    language_code=lang, device=self.whisper_cfg.device,
                )
            except Exception as e:
                log.warning("Alignment model load failed (%s) — alignment will be skipped", e)
                self._align_model = None

    def transcribe(self, audio_wav: Path) -> list[Word]:
        self.load()

        log.debug("Transcribing %s", audio_wav.name)
        # openai-whisper.transcribe(): processes the whole file synchronously
        # and returns a dict {"text", "segments", "language"}.
        result = self._model.transcribe(
            str(audio_wav),
            language=self.whisper_cfg.language,
            beam_size=self.whisper_cfg.beam_size,
            word_timestamps=True,
            condition_on_previous_text=False,  # avoids hallucination loops
            fp16=(self.whisper_cfg.compute_type in ("float16", "int8_float16")),
            # verbose=None: suppress both per-segment text dumps AND the tqdm
            # progress bar. False would keep the progress bar polluting our log.
            verbose=None,
        )
        log.info("Detected language: %s", result.get("language", "?"))

        raw_segments = result.get("segments", [])

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
    """Single-shot helper — for one-off use. Reloads models every call.

    Prefer instantiating Transcriber once and reusing it for batches.
    """
    t = Transcriber(whisper_cfg, alignment_cfg, batch_size=batch_size)
    try:
        return t.transcribe(audio_wav)
    finally:
        t.close()


def _flatten_words(segments) -> list[Word]:
    """Convert openai-whisper or whisperx segments into a flat list of Word."""
    out: list[Word] = []
    for seg in segments:
        # Both openai-whisper and whisperx yield dicts with these keys.
        for w in (seg.get("words") or []) if isinstance(seg, dict) else []:
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
