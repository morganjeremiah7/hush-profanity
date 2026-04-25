"""Command-line entry point: `hush scan` and `hush clean`."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import Settings
from . import clean as clean_mod
from .scanner import run


def _setup_logging(log_dir: Path, verbose: bool, name: str = "hush") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
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


def _cmd_scan(args, settings: Settings) -> int:
    _setup_logging(settings.paths.log_dir, args.verbose, "hush")
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


def _cmd_clean(args, settings: Settings) -> int:
    _setup_logging(settings.paths.log_dir, args.verbose, "hush-clean")
    log = logging.getLogger("hush.cli")

    if args.scope:
        roots = [Path(p) for p in args.scope]
    else:
        roots = settings.library.roots
    if not roots:
        log.error("No scope. Pass --scope PATH or set [library].roots in settings.toml.")
        return 2

    if args.include_all_srt:
        mode = "include-all-srt"
    elif args.include_cleaned_srt:
        mode = "include-cleaned-srt"
    else:
        mode = "basic"

    log.info("Scope: %s", [str(r) for r in roots])
    log.info("Mode: %s   Apply: %s", mode, args.apply)

    actions = clean_mod.find_actions(roots, settings.library.extensions, mode)
    summary = clean_mod.summarize(actions)
    if not actions:
        log.info("Nothing to delete. Library is already clean for this mode.")
        return 0

    log.info("Found %d sidecar file(s) to %s:",
             len(actions), "delete" if args.apply else "delete (DRY RUN — pass --apply to commit)")
    for reason, (n, b) in sorted(summary.items()):
        log.info("  %-12s  %5d files, %s bytes", reason, n, f"{b:,}")

    n_ok, n_fail, total_bytes = clean_mod.execute(actions, apply=args.apply)
    if args.apply:
        log.info("Deleted %d files (%s bytes); %d failures.", n_ok, f"{total_bytes:,}", n_fail)
    else:
        log.info("Dry run complete. Re-run with --apply to delete %d files (%s bytes).",
                 n_ok, f"{total_bytes:,}")
    return 0 if n_fail == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hush",
        description="Generate Kodi EDL files / clean subtitles for a video library.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to settings.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG level logging")
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("scan", help="Scan the configured library and write EDL/SRT sidecars.")

    p_clean = sub.add_parser(
        "clean",
        help="Delete leftover .edl / .srt sidecars from previous runs (dry-run by default).",
        description=(
            "Walks the configured library roots and deletes leftover sidecar files. "
            "Dry-run unless --apply is passed. By default only deletes <base>.edl and "
            "<base>-words.srt — never touches plain <base>.srt unless you opt in."
        ),
    )
    p_clean.add_argument("--scope", action="append", default=[],
                         help="Override the scope (folder to clean). May be passed multiple times. "
                              "If omitted, uses [library].roots from settings.toml.")
    p_clean.add_argument("--include-cleaned-srt", action="store_true",
                         help="Also delete <base>.srt IF a <base>.<lang>.srt sibling exists "
                              "(i.e., the official sub is preserved separately).")
    p_clean.add_argument("--include-all-srt", action="store_true",
                         help="Also delete every <base>.srt regardless. RISKY: would delete "
                              "official subs that are named <base>.srt without a language code.")
    p_clean.add_argument("--apply", action="store_true",
                         help="Actually delete the files. Without this flag, dry-run only.")

    args = parser.parse_args(argv)
    settings = Settings.load(args.config)

    cmd = args.cmd or "scan"
    if cmd == "scan":
        return _cmd_scan(args, settings)
    if cmd == "clean":
        return _cmd_clean(args, settings)

    parser.error(f"Unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
