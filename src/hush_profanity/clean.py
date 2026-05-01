"""Sidecar cleanup for re-running hush-profanity from scratch.

Behavior:

  .srt   — every .srt next to (or anywhere under) the configured library roots
           is deleted. No exceptions for .en.srt, .eng.srt, etc. The new
           pipeline does not consume official subs, so they don't need to stay.

  .edl   — examined first.
           If the file contains "skip-worthy" content (any action=0 entry
           anywhere, OR any entry inside a `Manual Skips` section) it is
           MOVED out of the library to a preserved-EDL folder
           (logs/preserved-edls/<timestamp>/<root-name>/<relative-path> by
           default, configurable via --preserved-dir). The file keeps its
           original name and relative directory layout under the destination,
           so you can tell at a glance which video and root each one came
           from. Moved EDLs won't be loaded by Kodi (different folder) and
           won't be merged into a fresh scan.

           Otherwise the .edl is DELETED outright.

A log file listing every preserved EDL — with the skip entries it contained,
human-readable so you can paste them into the new EDL or re-mark them in the
web UI — is written to logs/hush-clean-preserved-YYYYMMDD-HHMMSS.txt regardless
of dry-run/apply.

Defaults to dry-run; pass --apply to actually delete and move.
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


_ENTRY_RE = re.compile(r"^\s*([\d.]+)\s+([\d.]+)\s+(\d)\s*$")
_MANUAL_BEGIN = "###### Start Manual Skips section ######"
_MANUAL_END = "###### END Manual Skips section ######"
_AUTO_BEGIN = "###### Start Profanity Mutes section ######"
_AUTO_END = "###### END Profanity Mutes section ######"


@dataclass
class SkipEntry:
    start: float
    end: float
    action: int
    comment: str = ""
    section: str = ""  # "manual" | "other" | "auto" — for log clarity


@dataclass
class PreservedEdl:
    original: Path
    dest: Path
    skips: list[SkipEntry] = field(default_factory=list)


@dataclass
class CleanResult:
    srt_deleted: list[Path] = field(default_factory=list)
    edl_deleted: list[Path] = field(default_factory=list)
    edl_preserved: list[PreservedEdl] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)

    def total_files_touched(self) -> int:
        return len(self.srt_deleted) + len(self.edl_deleted) + len(self.edl_preserved)

    def total_bytes_freed(self) -> int:
        # Only deletions free bytes; renames don't.
        try:
            return sum(p.stat().st_size for p in (self.srt_deleted + self.edl_deleted)
                       if p.exists())
        except Exception:
            return 0


def _read_edl_skips(path: Path) -> list[SkipEntry]:
    """Return the list of skip-worthy entries in this .edl.

    "Skip-worthy" means:
        - any entry with action == 0 (cut/skip), wherever it appears, OR
        - any entry inside a Manual Skips section (regardless of action).
    """
    if not path.exists():
        return []
    skips: list[SkipEntry] = []
    section = "other"
    pending_comment = ""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    pending_comment = ""
                    continue
                if stripped == _MANUAL_BEGIN:
                    section = "manual"
                    pending_comment = ""
                    continue
                if stripped == _MANUAL_END:
                    section = "other"
                    pending_comment = ""
                    continue
                if stripped == _AUTO_BEGIN:
                    section = "auto"
                    pending_comment = ""
                    continue
                if stripped == _AUTO_END:
                    section = "other"
                    pending_comment = ""
                    continue
                if stripped.startswith("##"):
                    pending_comment = stripped.lstrip("#").strip()
                    continue
                m = _ENTRY_RE.match(stripped)
                if m:
                    start = float(m.group(1))
                    end = float(m.group(2))
                    action = int(m.group(3))
                    if section == "manual" or action == 0:
                        skips.append(SkipEntry(start=start, end=end, action=action,
                                               comment=pending_comment, section=section))
                    pending_comment = ""
                    continue
                pending_comment = ""
    except Exception as e:
        log.warning("Could not read %s: %s — treating as no-skip", path, e)
        return []
    return skips


def _resolve_dest(src: Path, root: Path, dest_root_dir: Path) -> Path:
    """Pick a non-colliding dest path under dest_root_dir / <src relative to root>."""
    candidate = dest_root_dir / src.relative_to(root)
    if not candidate.exists():
        return candidate
    parent = candidate.parent
    stem = candidate.stem
    sfx = candidate.suffix
    i = 1
    while True:
        candidate = parent / f"{stem}.{i}{sfx}"
        if not candidate.exists():
            return candidate
        i += 1


def plan(roots: list[Path], dest_dir: Path) -> CleanResult:
    """Decide what to do with each file. Does NOT touch the filesystem."""
    result = CleanResult()
    seen_root_names: dict[str, int] = {}
    for root in roots:
        if not root.exists():
            log.warning("Library root does not exist: %s", root)
            continue
        n = seen_root_names.get(root.name, 0)
        seen_root_names[root.name] = n + 1
        effective_root_name = root.name if n == 0 else f"{root.name}.{n}"
        per_root_dest = dest_dir / effective_root_name
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf == ".srt":
                result.srt_deleted.append(p)
            elif suf == ".edl":
                skips = _read_edl_skips(p)
                if skips:
                    result.edl_preserved.append(PreservedEdl(
                        original=p,
                        dest=_resolve_dest(p, root, per_root_dest),
                        skips=skips,
                    ))
                else:
                    result.edl_deleted.append(p)
    result.srt_deleted.sort()
    result.edl_deleted.sort()
    result.edl_preserved.sort(key=lambda x: x.original)
    return result


def execute(plan_result: CleanResult, apply: bool) -> CleanResult:
    """Run (or simulate) the plan. Mutates and returns the same CleanResult."""
    for p in plan_result.srt_deleted:
        log.info("%s [srt] %s", "DELETE" if apply else "WOULD DELETE", p)
        if apply:
            try:
                p.unlink()
            except Exception as e:
                log.error("delete failed: %s — %s", p, e)
                plan_result.failures.append((p, f"unlink: {e}"))

    for p in plan_result.edl_deleted:
        log.info("%s [edl no-skips] %s", "DELETE" if apply else "WOULD DELETE", p)
        if apply:
            try:
                p.unlink()
            except Exception as e:
                log.error("delete failed: %s — %s", p, e)
                plan_result.failures.append((p, f"unlink: {e}"))

    for pres in plan_result.edl_preserved:
        log.info("%s [edl with %d skips] %s -> %s",
                 "MOVE" if apply else "WOULD MOVE",
                 len(pres.skips), pres.original, pres.dest)
        if apply:
            try:
                pres.dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pres.original), str(pres.dest))
            except Exception as e:
                log.error("move failed: %s -> %s — %s", pres.original, pres.dest, e)
                plan_result.failures.append((pres.original, f"move: {e}"))

    return plan_result


def write_preserved_log(plan_result: CleanResult, log_dir: Path,
                        apply: bool, scope_roots: list[Path]) -> Path | None:
    """Write a human-readable log of preserved EDLs and their skip content.

    Returns the path written, or None if no EDLs were preserved.
    """
    if not plan_result.edl_preserved:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = log_dir / f"hush-clean-preserved-{stamp}.txt"
    lines: list[str] = []
    lines.append(f"hush-profanity preserved-EDL log")
    lines.append(f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ({'APPLIED' if apply else 'DRY RUN'})")
    lines.append(f"scope: {[str(r) for r in scope_roots]}")
    lines.append("")
    lines.append(f"{len(plan_result.edl_preserved)} EDL file(s) contained manual skip work and were "
                 f"{'moved out of the library' if apply else 'WOULD BE moved out of the library'}.")
    lines.append("")
    lines.append("Each block below shows the skip-worthy entries from that file. To carry them")
    lines.append("forward into the next scan, either:")
    lines.append("  (a) open the new <video>.edl in a text editor and paste these into its")
    lines.append("      'Start Manual Skips' section, OR")
    lines.append("  (b) reload the video in the manual-skip web UI and re-mark them.")
    lines.append("")
    lines.append("=" * 78)
    for i, pres in enumerate(plan_result.edl_preserved, 1):
        lines.append(f"[{i}] {pres.original}")
        lines.append(f"    {'->' if apply else '(would ->)'} {pres.dest}")
        lines.append(f"    {len(pres.skips)} skip-worthy entr{'ies' if len(pres.skips) != 1 else 'y'}:")
        for s in pres.skips:
            tag = f"section={s.section}"
            cmt = f"  // {s.comment}" if s.comment else ""
            lines.append(f"        {s.start:9.3f} -> {s.end:9.3f}   action={s.action}   ({tag}){cmt}")
        lines.append("=" * 78)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Wrote preserved-EDL log: %s", out)
    return out
