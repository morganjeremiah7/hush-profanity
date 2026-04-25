"""Command-line entry point: `hush scan`."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import Settings
from .scanner import run


def _setup_logging(log_dir: Path, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"hush-{time.strftime('%Y%m%d-%H%M%S')}.log"
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger(__name__).info("Logging to %s", log_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hush",
        description="Generate Kodi EDL files and clean subtitles for a video library.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to settings.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG level logging")
    sub = parser.add_subparsers(dest="cmd", required=False)
    sub.add_parser("scan", help="Scan the configured library and write EDL/SRT sidecars.")

    args = parser.parse_args(argv)
    settings = Settings.load(args.config)
    _setup_logging(settings.paths.log_dir, args.verbose)

    cmd = args.cmd or "scan"
    if cmd == "scan":
        results = run(settings)
        ok = sum(1 for r in results if r.ok)
        failed = [r for r in results if not r.ok]
        total_hits = sum(r.profanity_count for r in results if r.ok)
        log = logging.getLogger("hush.cli")
        log.info("Done. %d processed, %d failed, %d total profanity entries written.",
                 ok, len(failed), total_hits)
        for r in failed:
            log.error("  failed: %s — %s", r.path, r.error)
        return 0 if not failed else 1

    parser.error(f"Unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
