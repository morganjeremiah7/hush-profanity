"""Sidecar cleanup — delete old .edl / .srt files left over from previous runs.

The auto pass writes its sections into an existing .edl, preserving anything it
doesn't recognize as `other` lines. That means an .edl from a previous tool gets
its old entries kept verbatim alongside new entries — duplicating mutes. Same
risk for stale cleaned .srt files. This module deletes those leftovers.

Three safety levels:
  basic               — only `<base>.edl` and `<base>-words.srt` (always safe;
                        words.srt is a debug file, .edl was never written by
                        anything but a profanity tool).
  include-cleaned-srt — also `<base>.srt` IF a `<base>.<lang>.srt` sibling
                        exists. Reasoning: presence of an `.en.srt` proves the
                        official sub is preserved, so the bare `.srt` is the
                        old-tool output and safe to remove.
  include-all-srt     — also `<base>.srt` regardless. RISKY: would delete
                        official subs that happen to be named `<base>.srt`
                        (no language code).

Default is dry-run (lists actions only). Pass --apply to actually delete.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


OFFICIAL_SUFFIXES = (".en.srt", ".eng.srt", ".english.srt")


@dataclass
class CleanAction:
    target: Path
    bytes: int
    reason: str  # "edl" | "words.srt" | "cleaned.srt"


def _candidates_for(video: Path, mode: str) -> list[CleanAction]:
    actions: list[CleanAction] = []
    base = video.with_suffix("")

    edl = base.with_suffix(".edl")
    if edl.exists() and edl.is_file():
        actions.append(CleanAction(edl, edl.stat().st_size, "edl"))

    words_srt = video.parent / f"{video.stem}-words.srt"
    if words_srt.exists() and words_srt.is_file():
        actions.append(CleanAction(words_srt, words_srt.stat().st_size, "words.srt"))

    if mode in ("include-cleaned-srt", "include-all-srt"):
        cleaned = base.with_suffix(".srt")
        if cleaned.exists() and cleaned.is_file():
            if mode == "include-all-srt":
                actions.append(CleanAction(cleaned, cleaned.stat().st_size, "cleaned.srt"))
            else:
                # include-cleaned-srt: only if an official-named sibling exists
                has_official = any(
                    (video.parent / f"{video.stem}{suf}").exists()
                    for suf in OFFICIAL_SUFFIXES
                )
                if has_official:
                    actions.append(CleanAction(cleaned, cleaned.stat().st_size, "cleaned.srt"))

    return actions


def find_actions(roots: list[Path], extensions: list[str], mode: str) -> list[CleanAction]:
    """Walk roots, return all sidecar files that would be deleted in `mode`."""
    exts = {e.lower() for e in extensions}
    out: list[CleanAction] = []
    for root in roots:
        if not root.exists():
            log.warning("Library root does not exist: %s", root)
            continue
        for video in sorted(root.rglob("*")):
            if video.is_file() and video.suffix.lower() in exts:
                out.extend(_candidates_for(video, mode))
    return out


def execute(actions: list[CleanAction], apply: bool) -> tuple[int, int, int]:
    """Apply (or simulate) the deletions. Returns (n_deleted, n_failed, total_bytes)."""
    n_ok = 0
    n_fail = 0
    total = 0
    for a in actions:
        prefix = "DELETE" if apply else "WOULD DELETE"
        log.info("%s [%s] %s (%s bytes)", prefix, a.reason, a.target, f"{a.bytes:,}")
        total += a.bytes
        if not apply:
            n_ok += 1
            continue
        try:
            a.target.unlink()
            n_ok += 1
        except Exception as e:
            log.error("Failed to delete %s: %s", a.target, e)
            n_fail += 1
    return n_ok, n_fail, total


def summarize(actions: list[CleanAction]) -> dict[str, tuple[int, int]]:
    """Return {reason: (count, bytes)} for a clean overview."""
    out: dict[str, tuple[int, int]] = {}
    for a in actions:
        c, b = out.get(a.reason, (0, 0))
        out[a.reason] = (c + 1, b + a.bytes)
    return out
