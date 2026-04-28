"""Standalone subprocess worker that transcribes ONE file and exits.

Why this exists
---------------
ctranslate2 (the faster-whisper backend) has a long-standing bug on Windows
where its CUDA cleanup path corrupts the heap when a WhisperModel is destroyed
(OpenNMT/CTranslate2#1912, faster-whisper#71/#1293). After 1-3 model
destructions in a single process, the corruption gets touched fatally and
python.exe dies with a C++ exception followed by ucrtbase __fastfail.

We can't fix that bug from our side. The community-recommended workaround is
to run each transcription in its own process: when the process exits, the OS
guarantees a full CUDA context teardown that doesn't go through the buggy
cleanup path. This module is that worker.

Wire protocol
-------------
Invoked as:
    python -m hush_profanity._transcribe_worker <config.json> <words_out.json>

config.json schema (all paths absolute):
    {
        "wav_path":          "C:\\\\...\\\\audio.wav",
        "whisper": {  ... fields of WhisperCfg ...  },
        "alignment": { "enabled": true },
        "batch_size":         1
    }

words_out.json on success:
    [{"text": "...", "start": 0.0, "end": 0.4, "score": 0.99}, ...]

Exit codes:
    0  transcription completed and words_out.json is valid
    1  configuration / I/O error (parent should log + skip)
    2  transcription error inside Whisper (parent should log + skip)
   >2  unhandled exception (parent should log + skip)

The parent reads stderr of this process for diagnostic logging.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path


def _setup_logging() -> None:
    # All output goes to stderr so the parent can capture it without polluting stdout
    # (which is reserved for the words JSON if we ever switch to stdout-based IPC).
    logging.basicConfig(
        level=logging.INFO,
        format="[worker] %(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    log = logging.getLogger("hush.worker")
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        log.error("usage: python -m hush_profanity._transcribe_worker <config.json> <words_out.json>")
        return 1
    cfg_path = Path(args[0])
    out_path = Path(args[1])

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("could not read config %s: %s", cfg_path, e)
        return 1

    try:
        wav_path = Path(cfg["wav_path"])
        from .config import AlignmentCfg, WhisperCfg
        whisper_cfg = WhisperCfg(**{
            k: v for k, v in cfg["whisper"].items() if k in WhisperCfg.__annotations__
        })
        alignment_cfg = AlignmentCfg(**{
            k: v for k, v in cfg.get("alignment", {}).items()
            if k in AlignmentCfg.__annotations__
        })
        batch_size = int(cfg.get("batch_size", 1))
    except KeyError as e:
        log.error("missing required config key: %s", e)
        return 1
    except Exception as e:
        log.error("bad config: %s", e)
        return 1

    if not wav_path.exists():
        log.error("wav file does not exist: %s", wav_path)
        return 1

    try:
        # Import lazily so config errors are caught without paying the heavy
        # ctranslate2/whisperx import cost.
        from . import transcribe
        words = transcribe.transcribe_to_words(
            wav_path, whisper_cfg, alignment_cfg, batch_size=batch_size,
        )
    except Exception as e:
        log.exception("transcription failed: %s", e)
        return 2

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([asdict(w) for w in words]),
            encoding="utf-8",
        )
    except Exception as e:
        log.exception("could not write output %s: %s", out_path, e)
        return 1

    log.info("done: %d words -> %s", len(words), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
