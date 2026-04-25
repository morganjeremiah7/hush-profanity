"""Flask server for manual scene skip marking.

Endpoints:
    GET  /                         -> picker page
    GET  /api/library              -> JSON list of videos under configured roots
    GET  /api/edl?path=...         -> JSON of current auto + manual entries
    POST /api/edl?path=...         -> replace the manual section with posted entries
    GET  /watch?path=...           -> video player + timeline page
    GET  /stream?path=...          -> video stream with HTTP byte-range support
    GET  /poster?path=...          -> single-frame poster image (lazy ffmpeg call)

Path traversal: every `path` query parameter is resolved against the configured
library roots and rejected unless it lives inside one of them.
"""
from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import re
import subprocess
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file

from ..config import Settings
from ..edl import EdlEntry, EdlFile

log = logging.getLogger(__name__)


def create_app(settings: Settings) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    def _safe_resolve(raw: str) -> Path:
        p = Path(raw).resolve()
        for root in settings.library.roots:
            try:
                p.relative_to(root.resolve())
                return p
            except ValueError:
                continue
        abort(403, description=f"Path outside any configured library root: {p}")

    @app.route("/")
    def index():
        from flask import render_template
        return render_template("index.html",
                               default_action=settings.webui.default_action)

    @app.route("/watch")
    def watch():
        from flask import render_template
        raw = request.args.get("path", "")
        if not raw:
            abort(400, "missing path")
        video = _safe_resolve(raw)
        return render_template(
            "watch.html",
            video_path=str(video),
            video_name=video.name,
            default_action=settings.webui.default_action,
        )

    @app.route("/api/library")
    def api_library():
        exts = {e.lower() for e in settings.library.extensions}
        items = []
        for root in settings.library.roots:
            root_resolved = root.resolve()
            if not root_resolved.exists():
                continue
            for p in root_resolved.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    edl_path = p.with_suffix(".edl")
                    items.append({
                        "path": str(p),
                        "name": p.name,
                        "rel": str(p.relative_to(root_resolved)),
                        "root": str(root_resolved),
                        "size": p.stat().st_size,
                        "has_edl": edl_path.exists(),
                    })
        items.sort(key=lambda x: (x["root"], x["rel"]))
        return jsonify(items)

    @app.route("/api/edl", methods=["GET"])
    def api_edl_get():
        raw = request.args.get("path", "")
        if not raw:
            abort(400, "missing path")
        video = _safe_resolve(raw)
        edl_path = video.with_suffix(".edl")
        ef = EdlFile.read(edl_path, title=video.stem)
        return jsonify({
            "title": ef.title,
            "auto": [_entry_dict(e) for e in ef.auto],
            "manual": [_entry_dict(e) for e in ef.manual],
        })

    @app.route("/api/edl", methods=["POST"])
    def api_edl_post():
        raw = request.args.get("path", "")
        if not raw:
            abort(400, "missing path")
        video = _safe_resolve(raw)
        edl_path = video.with_suffix(".edl")
        body = request.get_json(silent=True) or {}
        manual_in = body.get("manual", [])
        manual = []
        for e in manual_in:
            try:
                start = float(e["start"])
                end = float(e["end"])
                action = int(e.get("action", settings.webui.default_action))
            except (KeyError, TypeError, ValueError):
                abort(400, "manual entries need numeric start/end and action")
            if end <= start:
                abort(400, f"end must be > start (got {start} -> {end})")
            manual.append(EdlEntry(
                start=start,
                end=end,
                action=action,
                comment=str(e.get("comment", "")).strip(),
            ))
        ef = EdlFile.read(edl_path, title=video.stem)
        ef.manual = manual
        ef.title = video.stem
        ef.write(edl_path)
        return jsonify({"ok": True, "manual_count": len(manual)})

    @app.route("/stream")
    def stream():
        raw = request.args.get("path", "")
        if not raw:
            abort(400, "missing path")
        video = _safe_resolve(raw)
        return _send_with_range(video)

    @app.route("/poster")
    def poster():
        raw = request.args.get("path", "")
        if not raw:
            abort(400, "missing path")
        video = _safe_resolve(raw)
        try:
            jpg = _generate_poster(video)
        except Exception as e:
            log.warning("poster failed for %s: %s", video, e)
            abort(404)
        return Response(jpg, mimetype="image/jpeg")

    return app


def _entry_dict(e: EdlEntry) -> dict:
    return {"start": e.start, "end": e.end, "action": e.action, "comment": e.comment}


_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _send_with_range(path: Path) -> Response:
    """Serve a video with HTTP byte-range support so HTML5 <video> can seek."""
    if not path.exists():
        abort(404)
    file_size = path.stat().st_size
    range_hdr = request.headers.get("Range", "")
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"

    if not range_hdr:
        # Whole-file fallback. Browsers will usually request a Range first anyway.
        return send_file(str(path), mimetype=mime, conditional=True)

    m = _RANGE_RE.match(range_hdr)
    if not m:
        abort(416)
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1
    end = min(end, file_size - 1)
    if start > end:
        abort(416)
    length = end - start + 1
    chunk_size = 1024 * 1024  # 1 MiB chunks

    def generate():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                buf = f.read(min(chunk_size, remaining))
                if not buf:
                    break
                yield buf
                remaining -= len(buf)

    rv = Response(generate(), status=206, mimetype=mime, direct_passthrough=True)
    rv.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    rv.headers["Accept-Ranges"] = "bytes"
    rv.headers["Content-Length"] = str(length)
    return rv


def _generate_poster(video: Path) -> bytes:
    """Grab a single frame from ~10% into the video as a JPEG."""
    duration = 60.0
    try:
        from ..audio import probe_duration
        d = probe_duration(video)
        if d:
            duration = d
    except Exception:
        pass
    seek = max(1.0, duration * 0.1)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin", "-loglevel", "error",
            "-ss", f"{seek:.2f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=320:-1",
            "-f", "image2", "-vcodec", "mjpeg",
            "pipe:1",
        ],
        capture_output=True, check=True,
    )
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hush-webui",
                                     description="Manual EDL scene-skip marker.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    settings = Settings.load(args.config)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    host = args.host or settings.webui.host
    port = args.port or settings.webui.port
    app = create_app(settings)
    log.info("Manual EDL editor: http://%s:%d/", host, port)
    # threaded=True so streaming a big video doesn't block the API endpoints.
    app.run(host=host, port=port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
