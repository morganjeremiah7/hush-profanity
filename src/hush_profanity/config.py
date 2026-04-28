"""Settings loading.

settings.toml is the canonical config; settings.example.toml is the committed template.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class LibraryCfg:
    roots: list[Path]
    extensions: list[str]
    skip_if_processed: bool = True


@dataclass
class WhisperCfg:
    model: str = "large-v3"
    compute_type: str = "float16"
    device: str = "cuda"
    device_index: int = 0
    beam_size: int = 5
    language: str = "en"
    vad_filter: bool = True
    audio_language: str = "eng"


@dataclass
class AlignmentCfg:
    enabled: bool = True


@dataclass
class EdlCfg:
    profanity_action: int = 1
    padding_seconds: float = 0.10
    merge_gap_seconds: float = 2.0


@dataclass
class SubtitlesCfg:
    generate_srt: bool = True
    generate_words_srt: bool = False
    # Hard cap on a single cue's duration. Long monologues with no punctuation
    # get split here for readability.
    segment_max_duration: float = 5.0
    # If the gap between consecutive words exceeds this many seconds, finalize
    # the cue. Stops cues from spanning long silences (sentence boundaries,
    # speaker changes, scene cuts).
    max_pause_seconds: float = 1.5
    # How long a finalized cue stays on screen after the last word ends, so the
    # viewer has time to finish reading. Capped so we don't crowd the next cue.
    tail_seconds: float = 0.5


@dataclass
class PathsCfg:
    swears_file: Path
    phrases_file: Path
    replacements_file: Path
    log_dir: Path
    checkpoint_file: Path


@dataclass
class WebUiCfg:
    port: int = 8765
    host: str = "127.0.0.1"
    default_action: int = 0


@dataclass
class PerformanceCfg:
    # Number of CPU threads running ffmpeg in parallel, ahead of the GPU.
    encode_workers: int = 2
    # Number of GPU worker threads. Each spawns a fresh subprocess per file
    # (model loaded inside the subprocess). 1 = serial transcription (safe).
    # 2 = two files transcribed concurrently on the same GPU (~1.5-1.8x faster
    # in our setup; needs ~14-22 GB VRAM at peak depending on compute_type).
    # Higher values rarely help — diminishing returns from GPU SM contention.
    gpu_workers: int = 1
    # Number of CPU threads doing post-processing (profanity detection + EDL/SRT writes).
    post_workers: int = 2
    # Ignored under openai-whisper (no batched API). Kept for backwards
    # compatibility with old settings.toml files; was meaningful for the
    # faster-whisper engine.
    whisper_batch_size: int = 1


@dataclass
class Settings:
    library: LibraryCfg
    whisper: WhisperCfg
    alignment: AlignmentCfg
    edl: EdlCfg
    subtitles: SubtitlesCfg
    paths: PathsCfg
    webui: WebUiCfg
    performance: PerformanceCfg
    project_root: Path

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        project_root = _find_project_root()
        if path is None:
            path = project_root / "config" / "settings.toml"
            if not path.exists():
                example = project_root / "config" / "settings.example.toml"
                raise FileNotFoundError(
                    f"No settings.toml found. Copy {example} to {path} and edit it."
                )
        with open(path, "rb") as f:
            data = tomllib.load(f)

        def resolve(p: str) -> Path:
            pp = Path(p)
            return pp if pp.is_absolute() else (project_root / pp).resolve()

        lib = data.get("library", {})
        wh = data.get("whisper", {})
        al = data.get("alignment", {})
        ed = data.get("edl", {})
        sub = data.get("subtitles", {})
        pa = data.get("paths", {})
        wui = data.get("webui", {})

        return cls(
            library=LibraryCfg(
                roots=[Path(r) for r in lib.get("roots", [])],
                extensions=[e.lower() for e in lib.get("extensions", [".mp4", ".mkv"])],
                skip_if_processed=lib.get("skip_if_processed", True),
            ),
            whisper=WhisperCfg(**{k: v for k, v in wh.items() if k in WhisperCfg.__annotations__}),
            alignment=AlignmentCfg(**{k: v for k, v in al.items() if k in AlignmentCfg.__annotations__}),
            edl=EdlCfg(**{k: v for k, v in ed.items() if k in EdlCfg.__annotations__}),
            subtitles=SubtitlesCfg(**{k: v for k, v in sub.items() if k in SubtitlesCfg.__annotations__}),
            paths=PathsCfg(
                swears_file=resolve(pa.get("swears_file", "config/swears.txt")),
                phrases_file=resolve(pa.get("phrases_file", "config/swear_phrases.txt")),
                replacements_file=resolve(pa.get("replacements_file", "config/replacements.json")),
                log_dir=resolve(pa.get("log_dir", "logs")),
                checkpoint_file=resolve(pa.get("checkpoint_file", "logs/checkpoint.json")),
            ),
            webui=WebUiCfg(**{k: v for k, v in wui.items() if k in WebUiCfg.__annotations__}),
            performance=PerformanceCfg(**{
                k: v for k, v in data.get("performance", {}).items()
                if k in PerformanceCfg.__annotations__
            }),
            project_root=project_root,
        )


def _find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def load_swear_words(path: Path) -> set[str]:
    """Return a set of lowercase swear words. Lines starting with # are comments."""
    words: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip().lower()
            if s and not s.startswith("#"):
                words.add(s)
    return words


def load_phrase_lines(path: Path) -> list[str]:
    """Return raw phrase strings (lowercase, comments stripped). Tokenization is the matcher's job."""
    out: list[str] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip().lower()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def load_replacements(path: Path) -> tuple[dict[str, str], dict[str, str], str, str]:
    """Return (word_map, phrase_map, word_default, phrase_default).

    Supports the v0.2 nested format ({"words": {...}, "phrases": {...}}) and the
    v0.1 flat format ({"fuck": "fudge", ...}) for backwards compatibility.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    word_default = data.get("_default", "...")
    phrase_default = data.get("_phrase_default", word_default)
    if "words" in data and isinstance(data["words"], dict):
        word_map = dict(data["words"])
    else:
        word_map = {k: v for k, v in data.items()
                    if not k.startswith("_") and isinstance(v, str)}
    phrase_map = dict(data.get("phrases", {})) if isinstance(data.get("phrases"), dict) else {}
    return word_map, phrase_map, word_default, phrase_default
