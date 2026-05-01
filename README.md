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
   │   openai-whisper (large-v3, fp16, fresh subprocess per file) ──► segments + word timestamps
   │       │
   │       ▼
   │   WhisperX wav2vec2 alignment ──► word timestamps refined to ±20 ms
   │       │
   │       ├──► profanity detector (lowercase, suffix-aware, multi-word phrases) ──► EDL mute entries
   │       │
   │       └──► swap swears for family-friendly substitutes ──► cleaned subtitles
   │
   ├──► <video>.edl  (auto + manual sections)
   └──► <video>.srt
```

A 3-stage parallel pipeline (CPU encode → GPU transcribe+align → CPU write) keeps the GPU fed across the whole library. Each transcription runs in a fresh Python subprocess, so a CUDA hiccup on one file can't poison the rest of the run. A checkpoint is saved after every successful file — Ctrl+C is safe (one press = graceful drain, two = hard exit).

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

- **Windows 10/11.** Should work on Linux if you install all the dependencies. (Untested.)
- **Python 3.10, 3.11, or 3.12**
- **NVIDIA GPU** with ≥8 GB VRAM and a CUDA 12.x-capable driver
- **ffmpeg** on PATH. Easiest install: `winget install Gyan.FFmpeg`, then open a fresh terminal

### GPU tiers (the installer auto-detects your VRAM and picks the right defaults)

| VRAM | Model auto-picked | Concurrent files | What you get |
|---|---|---|---|
| **24 GB+** (3090, 4090, 5090, A6000) | `large-v3` | 2 | Best accuracy + speed boost from running two files in parallel on the GPU |
| **12-23 GB** (3060 12GB, 4070, 4070 Ti Super 16GB, etc.) | `large-v3` | 1 | Best accuracy, single-file pace |
| **8-11 GB** (3060 8GB, 4060, 4060 Ti 8GB, etc.) | `medium` | 1 | Slightly lower transcription accuracy but the same precise EDL timestamps from wav2vec2 alignment; safely within VRAM |
| **<8 GB** | not officially supported | 1 | Will likely OOM on real content; install proceeds with conservative defaults but no guarantees |
| **No GPU / CPU only** | — | — | Will run, but ~50× slower than even the slowest GPU tier; not recommended for batch use |

The installer reads your GPU's VRAM at install time and writes the right `model` and `gpu_workers` values into `config/settings.toml`. You can override these manually after install.

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

That walks the configured roots, processes each unprocessed file, and writes sidecars next to the source. Safe to interrupt with Ctrl+C — a checkpoint is saved after every successful file, so re-running picks up where it left off. Files that errored or were interrupted are **not** checkpointed, so they're retried automatically on the next run.

Each file gets a clear lifecycle line in the log:

```
[1234/7605] BEGIN: Y:\movies\Coco.mp4
[1234/7605] DONE:  Y:\movies\Coco.mp4 (wall=2158.4s, 1 hits)
[1235/7605] FAIL:  Y:\movies\BrokenFile.mp4 (transcribe-timeout: ...) wall=3601.2s — will retry on next run
[1236/7605] SKIP:  Y:\movies\Already_done.mp4 (already has auto EDL)
```

`grep DONE:` / `grep FAIL:` / `grep SKIP:` over the log gives a quick run summary.

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

### What happens to existing sidecars during a scan

You usually don't need to clean up anything before scanning — the scanner handles existing sidecars on its own:

- **Existing `.srt` (e.g. official subtitles you downloaded)** — the first time hush-profanity processes a video that already has a `<base>.srt` next to it, the existing file is renamed to `<base>.original.srt` before the new clean subtitle is written. Subsequent re-scans of the same video overwrite our `<base>.srt` freely. Your originals are preserved without any pre-cleanup step.
- **Existing `.edl`** — the EDL file format is sectioned. The scanner only rewrites the `Profanity Mutes` section; anything in the `Manual Skips` section (or in any other section you've added) is preserved across re-runs. Manual skip work added through the web tool stays put.
- **Failed files** — if a file errors out during a scan (timeout, transient I/O error, you Ctrl+C, etc.), it is **not** added to the checkpoint. The next run will retry it automatically. The log marks every file with `[N/TOTAL] BEGIN: ...` / `DONE: ...` / `FAIL: ... — will retry on next run` so you can see exactly what's happened to each file.

### Clean (advanced — bulk reset of sidecars)

If you do want to wipe everything and start over (e.g. after a major model upgrade):

```cmd
windows\clean.bat                       REM dry-run, scope = settings.toml [library].roots
windows\clean.bat --apply               REM commit
windows\clean.bat --scope "Y:\Movies"   REM dry-run on a specific folder
```

Walks the configured roots and:
- deletes every `.srt`,
- deletes any `.edl` whose only entries are auto profanity mutes,
- **moves** any `.edl` containing manual skip work (any `action=0` entry, or anything inside the `Manual Skips` section) into `logs\preserved-edls\<timestamp>\<root-name>\<rel-path>\` so they're easy to find and won't be loaded by Kodi or merged into a fresh scan.

A human-readable index of every preserved EDL — with its skip ranges so you can paste them back later — is written to `logs\hush-clean-preserved-<timestamp>.txt`. Always dry-run unless `--apply` is passed.

Most users never need this — the in-scanner preservation behavior above covers the typical workflow.

### Other ways to invoke

```cmd
.venv\Scripts\python.exe -m hush_profanity scan
.venv\Scripts\python.exe -m hush_profanity clean
.venv\Scripts\python.exe -m hush_profanity.webui.server --port 8080
```

## Configuration

`config\settings.toml` is the only file you need to edit. Every key is documented inline in the file (also see `config\settings.example.toml` for the full template). The table below highlights the keys you'll most often touch — see the example TOML for the rest (encode/post worker counts, beam size, subtitle cue limits, paths, etc.).

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
| `[performance].gpu_workers` | `1` (24 GB+ → `2`) | How many files transcribe in parallel on the GPU. Set by the installer based on detected VRAM. |
| `[webui].port` | `8765` | HTTP port for the manual editor. |
| `[webui].default_action` | `0` | Default EDL action for manual entries — `0` cut, `1` mute. |

Two more files in `config/`:

- `swears.txt` — one word per line, lowercase. Comments start with `#`. Edit freely; reload happens at scan start.
- `replacements.json` — mapping of swear → family-friendly substitute used in the cleaned `.srt`. Anything in `swears.txt` but not in `replacements.json` is replaced with the `_default` value (`...` by default). The EDL mute is generated regardless of whether a replacement exists.

## Repository layout

```
config/                       swears.txt, swear_phrases.txt, replacements.json, settings.example.toml
scripts/                      install-windows.ps1
src/hush_profanity/
    __main__.py               python -m hush_profanity ...
    cli.py                    `hush` entry point — scan / clean subcommands, two-strike Ctrl+C
    config.py                 settings loader
    audio.py                  ffmpeg-based audio extraction + track selection
    transcribe.py             openai-whisper + WhisperX wav2vec2 alignment
    _transcribe_worker.py     standalone subprocess worker — loads model in fresh Python per file
    profanity.py              word + multi-word phrase swear detection
    srt.py                    cleaned subtitle writer
    edl.py                    sectioned EDL read/write (auto + manual sections)
    scanner.py                library walker + 3-stage parallel pipeline + checkpointing
    clean.py                  sidecar cleanup (delete .srt / auto-only .edl, move skip-bearing .edl)
    webui/
        server.py             Flask server with byte-range streaming
        templates/            index.html, watch.html
        static/               style.css, index.js, watch.js
windows/                      install.bat, scan.bat, manual-skip.bat, clean.bat
```

## Troubleshooting

- **CUDA out of memory.** Drop `[performance].gpu_workers = 1` in `config\settings.toml`. If you're already at 1, set `[whisper].compute_type = "int8_float16"` (openai-whisper accepts this and falls back to fp16 internally — saves a little allocator headroom).
- **Files getting "timed out (>3600s)" in the log.** The hard ceiling per file is 60 minutes (see `SUBPROCESS_TIMEOUT_SECONDS` in `src/hush_profanity/scanner.py`). On a 3090 with `gpu_workers=2`, even 2-hour films finish in ~30–40 min. If you're hitting the cap, you're either on a slower GPU or an unusually long file — bump the constant, or drop `gpu_workers` to 1 so each file gets the whole GPU (per-file completes faster, at the cost of overall throughput). Files that time out are not added to the checkpoint and will retry automatically on the next run.
- **`ffmpeg.exe` not found.** `winget install Gyan.FFmpeg`, then close and reopen the terminal.
- **Slow on first run.** Whisper downloads the `large-v3` model (~3 GB) into your user cache the first time it loads. Subsequent runs reuse the cache.
- **Wrong audio track on .mkv.** Set `[whisper].audio_language` to the right ISO 639-2 code (`eng`, `spa`, `fre`…). hush-profanity picks the first audio stream tagged with that language.
- **Profanity in music or silence.** That's a Whisper hallucination. openai-whisper has no integrated VAD (the `vad_filter` setting from older configs is silently ignored). The current line of defense is its built-in `no_speech_threshold` and the swear-list itself — add false-positive triggers to a comment or remove them from `config\swears.txt`.
- **Closing your terminal kills the scan.** Run `windows\scan.bat` from a standalone terminal (not VS Code's integrated terminal), or launch via `start "" cmd /k windows\scan.bat`. Closing the parent console sends `CTRL_CLOSE_EVENT` to all children — same effect as Ctrl+C.

## License

MIT — see [LICENSE](LICENSE).
