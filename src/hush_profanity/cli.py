"""Command-line entry point: `hush scan` and `hush clean`."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .config import Settings
from . import clean as clean_mod
from .scanner import run


def _install_two_strike_sigint() -> None:
    """First Ctrl+C drains the pipeline gracefully (in-flight files finish);
    second Ctrl+C calls os._exit and abandons in-flight GPU work.

    Without this, Ctrl+C in a long-running scan blocks until the current
    transcription completes — minutes per file with no visible response.
    """
    counter = [0]

    def handler(sig, frame):
        counter[0] += 1
        if counter[0] >= 2:
            print(
                "\n[hush] Force exit (second Ctrl+C). In-flight files lost; "
                "checkpoint preserved.",
                flush=True,
            )
            os._exit(130)
        print(
            "\n[hush] Ctrl+C — finishing in-flight files. "
            "Press Ctrl+C again to force quit.",
            flush=True,
        )
        # Re-raise as KeyboardInterrupt so the producer loop drops out cleanly.
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)


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
    _install_two_strike_sigint()
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

    log.info("Scope: %s", [str(r) for r in roots])
    log.info("Apply: %s", args.apply)

    plan = clean_mod.plan(roots)
    n_total = plan.total_files_touched()
    if n_total == 0:
        log.info("Nothing to do. No .srt or .edl files found under scope.")
        return 0

    verb = "delete" if args.apply else "delete (DRY RUN — pass --apply to commit)"
    log.info("Plan:")
    log.info("  .srt files to %s:                %d", verb, len(plan.srt_deleted))
    log.info("  .edl files to %s (no skips):    %d", verb, len(plan.edl_deleted))
    log.info("  .edl files to %s (have skips):  %d",
             "rename to .edl.preserved" if args.apply else "rename (DRY RUN)",
             len(plan.edl_preserved))

    clean_mod.execute(plan, apply=args.apply)
    log_path = clean_mod.write_preserved_log(plan, settings.paths.log_dir,
                                             apply=args.apply, scope_roots=roots)
    if log_path:
        log.info("Preserved-EDL details: %s", log_path)

    if plan.failures:
        log.error("%d failure(s):", len(plan.failures))
        for p, err in plan.failures:
            log.error("  %s — %s", p, err)
        return 1
    if args.apply:
        log.info("Done. Deleted %d, renamed %d, freed ~%s bytes.",
                 len(plan.srt_deleted) + len(plan.edl_deleted),
                 len(plan.edl_preserved),
                 f"{plan.total_bytes_freed():,}")
    else:
        log.info("Dry run complete. Re-run with --apply to commit (%d files would be touched).",
                 n_total)
    return 0


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
        help="Delete leftover .srt / .edl sidecars from previous runs (dry-run by default).",
        description=(
            "Walks the configured library roots recursively and:\n"
            "  - deletes every .srt file (no exceptions),\n"
            "  - deletes any .edl file that contains no skip-worthy entries,\n"
            "  - renames .edl files that DO contain manual skip work to "
            "<base>.edl.preserved so they stay in their directory but won't "
            "be loaded by Kodi or merged into a fresh scan,\n"
            "  - writes a human-readable log of every preserved EDL to "
            "logs/hush-clean-preserved-*.txt.\n\n"
            "Dry-run unless --apply is passed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_clean.add_argument("--scope", action="append", default=[],
                         help="Override the scope (folder to clean). May be passed multiple times. "
                              "If omitted, uses [library].roots from settings.toml.")
    p_clean.add_argument("--apply", action="store_true",
                         help="Actually delete and rename. Without this flag, dry-run only.")

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
