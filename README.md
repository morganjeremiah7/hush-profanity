# hush-profanity

Family-safe playback for your Kodi library. Whisper transcribes your videos, generates EDL mute regions for profanity and clean subtitles automatically, plus a web tool to mark scenes to skip.

## What it does

For every video in the library you point it at, hush-profanity produces two sidecar files next to the source:

- **`<video>.edl`** — Kodi-compatible Edit Decision List. The auto-pass writes one entry per detected swear (action `1` = mute by default). Auto and manual entries live in the same file in two clearly-marked sections; re-running the scanner only rewrites the auto section, so manual entries you add through the web tool are preserved.
- **`<video>.srt`** — Cleaned subtitles built from Whisper's transcription with swears swapped for family-friendly substitutes (e.g. *fuck → fudge*, *shit → shoot*).

A separate web tool lets you scrub through any video, mark in/out points with hotkeys, and append manual skip ranges (nudity, violence, anything else) to the same EDL.

## How it works

```
video.mp4
   │
   ├──► extract 16 kHz mono WAV (one audio track, optionally selected by language)
   │       │
   │       ▼
   │   faster-whisper (large-v3, fp16) ──► segments + word timestamps
   │       │
   │       ▼
   │   WhisperX wav2vec2 alignment ──► word timestamps refined to ±20 ms
   │       │
   │       ├──► profanity detector (lowercase, suffix-aware) ──► EDL mute entries
   │       │
   │       └──► swap swears for family-friendly substitutes ──► cleaned subtitles
   │
   ├──► <video>.edl  (auto + manual sections)
   └──► <video>.srt
```

## EDL-aware playback (which players actually honor `.edl` files)

EDL files only do anything if your player loads them. The big one for self-hosted libraries:

- **Kodi** — supports EDL out of the box, no addon needed. Drop `<video>.edl` next to `<video>.mp4` and it just works on next playback. ([Kodi EDL wiki](https://kodi.wiki/view/Edit_decision_list))
- **MPC-HC / MPC-BE** — supports EDL via included scripts.
- **mpv** — supports it through community Lua scripts (e.g. `edl.lua`, `mpv-skip-silence`); not built in.

Players that **do not** honor EDL natively:

- **Plex Media Server / Plex apps** — the player ignores EDL files. (This is exactly why this project targets Kodi.)
- **Jellyfin** — no built-in EDL support at the time of writing.
- **VLC** — no EDL support.

## Requirements

- **Windows 10/11** (Linux supported via the same Python package, but launchers/install script are Windows-first)
- **Python 3.10, 3.11, or 3.12**
- **NVIDIA GPU** with ≥8 GB VRAM and a CUDA 12.x-capable driver. Tested on RTX 3090 (24 GB). Will fall back to CPU if no GPU is available, but expect that to be ~50× slower.
- **ffmpeg** on PATH. Easiest install: `winget install Gyan.FFmpeg`, then open a fresh terminal.

## Install (Windows)

```cmd
git clone https://github.com/morganjeremiah7/hush-profanity.git
cd hush-profanity
windows\install.bat
```

The installer creates a `.venv\` next to the project, installs PyTorch with the CUDA 12.1 wheel, installs the rest of the dependencies in editable mode, and copies `config\settings.example.toml` to `config\settings.toml` if it doesn't exist.

After install, **edit `config\settings.toml`** — at minimum, set `[library].roots` to point at your video folders.

## Usage

### Auto-scan (mute swearing + generate clean subtitles)

```cmd
windows\scan.bat
```

That walks the configured roots, processes each unprocessed file, and writes sidecars next to the source. Safe to interrupt with Ctrl+C — a checkpoint is saved after every file, so re-running picks up where it left off.

### Manual scene-skip editor (web UI)

```cmd
windows\manual-skip.bat
```

Opens `http://127.0.0.1:8765/` in your default browser. Pick a video → scrub the timeline → press `I` to mark in, `O` to mark out, `Enter` to add the entry, then `Save EDL` to persist. The auto-detected profanity entries are shown read-only on the same page.

Hotkeys in the player:

| Key | Action |
|---|---|
| `Space` | play/pause |
| `J` / `L` | jump back / forward 5 s |
| `,` / `.` | step back / forward 0.1 s |
| `I` / `O` | mark in / out at current time |
| `Enter` | append entry from in/out |
| `Esc` | clear marks |

### Other ways to invoke

```cmd
.venv\Scripts\python.exe -m hush_profanity scan
.venv\Scripts\python.exe -m hush_profanity.webui.server --port 8080
```

## Configuration

`config\settings.toml` is the only file you need to edit. Documented inline; the most important keys:

| Key | Default | What it does |
|---|---|---|
| `[library].roots` | — | List of folders to scan recursively. |
| `[library].extensions` | `.mp4 .mkv .avi .mov .m4v` | File suffixes to consider videos. |
| `[library].skip_if_processed` | `true` | Skip files that already have an auto EDL section. Set `false` to force a full re-scan. |
| `[whisper].model` | `large-v3` | `large-v3` (most accurate), `large-v3-turbo` (faster, slightly less accurate), `medium`, etc. |
| `[whisper].compute_type` | `float16` | `float16` (3090 default), `int8_float16` (≈half the VRAM), `int8` (CPU). |
| `[whisper].audio_language` | `eng` | Preferred audio track language for multi-track .mkv files. |
| `[alignment].enabled` | `true` | wav2vec2 forced alignment for ±20 ms word timestamps. Strongly recommended. |
| `[edl].profanity_action` | `1` | `1` mute, `0` cut entirely. |
| `[edl].padding_seconds` | `0.10` | Pad each mute region by this much on either side. |
| `[edl].merge_gap_seconds` | `2.0` | Merge mutes within this many seconds of each other. |
| `[webui].port` | `8765` | HTTP port for the manual editor. |
| `[webui].default_action` | `0` | Default EDL action for manual entries — `0` cut, `1` mute. |

Two more files in `config/`:

- `swears.txt` — one word per line, lowercase. Comments start with `#`. Edit freely; reload happens at scan start.
- `replacements.json` — mapping of swear → family-friendly substitute used in the cleaned `.srt`. Anything in `swears.txt` but not in `replacements.json` is replaced with the `_default` value (`...` by default). The EDL mute is generated regardless of whether a replacement exists.

## Repository layout

```
config/                  swears.txt, replacements.json, settings.example.toml
scripts/                 install-windows.ps1
src/hush_profanity/
    __main__.py          python -m hush_profanity ...
    cli.py               `hush` entry point
    config.py            settings loader
    audio.py             ffmpeg-based audio extraction + track selection
    transcribe.py        faster-whisper + WhisperX alignment
    profanity.py         word-level swear detection
    srt.py               cleaned subtitle writer
    edl.py               sectioned EDL read/write
    scanner.py           library walker + per-file pipeline + checkpointing
    webui/
        server.py        Flask server with byte-range streaming
        templates/       index.html, watch.html
        static/          style.css, index.js, watch.js
windows/                 install.bat, scan.bat, manual-skip.bat
```

## Troubleshooting

- **CUDA out of memory.** Set `[whisper].compute_type = "int8_float16"`. That cuts VRAM roughly in half.
- **`ffmpeg.exe` not found.** `winget install Gyan.FFmpeg`, then close and reopen the terminal.
- **Slow on first run.** Whisper downloads the `large-v3` model (~3 GB) into your user cache the first time it loads. Subsequent runs reuse the cache.
- **Wrong audio track on .mkv.** Set `[whisper].audio_language` to the right ISO 639-2 code (`eng`, `spa`, `fre`…). hush-profanity picks the first audio stream tagged with that language.
- **Profanity in music or silence.** That's a Whisper hallucination. Make sure `[whisper].vad_filter = true` (default).

## License

MIT — see [LICENSE](LICENSE).
