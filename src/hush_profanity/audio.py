"""Audio extraction.

Whisper and the wav2vec2 aligner both expect 16 kHz mono PCM. Extracting once and
reusing avoids decoding the video twice and lets us pick a specific audio track on
multi-track .mkv files.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class AudioExtractError(RuntimeError):
    pass


def select_audio_track(video_path: Path, prefer_language: str = "eng") -> int:
    """Return the ffmpeg audio stream index that best matches `prefer_language`.

    Falls back to 0 (first audio stream) if nothing matches or ffprobe fails.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index:stream_tags=language,title",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(out.stdout).get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        log.warning("ffprobe failed for %s: %s — defaulting to track 0", video_path, e)
        return 0

    if not streams:
        return 0

    prefer = prefer_language.lower()
    for i, s in enumerate(streams):
        tags = (s.get("tags") or {})
        if (tags.get("language") or "").lower() == prefer:
            return i
    return 0


def extract_wav(video_path: Path, out_wav: Path, audio_index: int = 0) -> None:
    """Extract one audio track from a video as 16 kHz mono PCM."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(video_path),
        "-map", f"0:a:{audio_index}",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AudioExtractError(
            f"ffmpeg failed extracting audio from {video_path}: {proc.stderr.strip()}"
        )


def probe_duration(video_path: Path) -> float | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception as e:
        log.warning("Could not probe duration for %s: %s", video_path, e)
        return None
