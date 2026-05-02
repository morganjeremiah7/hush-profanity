"""Kodi EDL read/write.

Kodi EDL format (per row):
    <start_seconds>\t<end_seconds>\t<action>

Actions: 0 = cut, 1 = mute, 2 = scene marker, 3 = commercial.

We split a single .edl into two clearly-labeled sections so the auto-scanner can
rewrite its own section without clobbering manual scene-skip entries:

    ##<basename>
    ###### Start Profanity Mutes section ######
    ## comment lines about each entry
    1.234\t5.678\t1
    ###### END Profanity Mutes section ######
    ###### Start Manual Skips section ######
    100.000\t125.000\t0
    ###### END Manual Skips section ######

Re-running the auto pass replaces only the profanity section. The manual section is
preserved verbatim (we never touch user-edited lines).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

log = logging.getLogger(__name__)

AUTO_BEGIN = "###### Start Profanity Mutes section ######"
AUTO_END = "###### END Profanity Mutes section ######"
MANUAL_BEGIN = "###### Start Manual Skips section ######"
MANUAL_END = "###### END Manual Skips section ######"

_ENTRY_RE = re.compile(r"^\s*([\d.]+)\s+([\d.]+)\s+(\d)\s*$")


_ACTION_LABEL = {0: "Skipped", 1: "Muted", 2: "Scene", 3: "Commercial"}


@dataclass
class EdlEntry:
    start: float
    end: float
    action: int  # 0=cut, 1=mute, 2=scene, 3=commercial
    comment: str = ""

    def to_lines(self) -> list[str]:
        """Render this entry as one or more lines of EDL text.

        Always emits a `##` comment line above the entry with a human-readable
        timestamp range. If `self.comment` already contains the timestamp range
        (e.g. an auto-mute comment built by entries_from_profanity_hits, which
        looks like 'Muted: 0:01:23 to 0:01:24 <context words>'), it's used as-is.
        Otherwise we generate a label like 'Skipped: 0:01:40 to 0:02:05' and
        append the user's note (if any) as a free-text suffix.
        """
        ts = f"{_hms(self.start)} to {_hms(self.end)}"
        label = _ACTION_LABEL.get(self.action, f"Action-{self.action}")
        existing = (self.comment or "").strip()
        if ts in existing:
            comment_text = existing
        elif existing:
            comment_text = f"{label}: {ts} — {existing}"
        else:
            comment_text = f"{label}: {ts}"
        return [
            f"##{comment_text}",
            f"{self.start:.3f}\t{self.end:.3f}\t{self.action}",
        ]


@dataclass
class EdlFile:
    title: str  # the ##<basename> header
    auto: list[EdlEntry]
    manual: list[EdlEntry]
    other: list[str]  # any free-text lines outside our two sections

    @classmethod
    def empty(cls, title: str) -> "EdlFile":
        return cls(title=title, auto=[], manual=[], other=[])

    @classmethod
    def read(cls, path: Path, title: str | None = None) -> "EdlFile":
        if not path.exists():
            return cls.empty(title or path.stem)

        with open(path, encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f]

        auto: list[EdlEntry] = []
        manual: list[EdlEntry] = []
        other: list[str] = []
        found_title = title
        section: str | None = None  # None | "auto" | "manual"
        pending_comment = ""

        for ln in lines:
            stripped = ln.strip()
            if not stripped:
                pending_comment = ""
                continue
            if stripped == AUTO_BEGIN:
                section = "auto"
                pending_comment = ""
                continue
            if stripped == AUTO_END:
                section = None
                pending_comment = ""
                continue
            if stripped == MANUAL_BEGIN:
                section = "manual"
                pending_comment = ""
                continue
            if stripped == MANUAL_END:
                section = None
                pending_comment = ""
                continue
            if stripped.startswith("##"):
                comment_text = stripped.lstrip("#").strip()
                if section is None and found_title is None:
                    found_title = comment_text
                else:
                    pending_comment = comment_text
                continue
            m = _ENTRY_RE.match(stripped)
            if m:
                entry = EdlEntry(
                    start=float(m.group(1)),
                    end=float(m.group(2)),
                    action=int(m.group(3)),
                    comment=pending_comment,
                )
                pending_comment = ""
                if section == "auto":
                    auto.append(entry)
                elif section == "manual":
                    manual.append(entry)
                else:
                    # Entry outside any of our sections — preserve as raw text so we don't lose it.
                    other.append(ln)
                continue
            # Unrecognized line — preserve verbatim.
            other.append(ln)

        return cls(
            title=found_title or path.stem,
            auto=auto,
            manual=manual,
            other=other,
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out: list[str] = [f"##{self.title}"]
        out.append(AUTO_BEGIN)
        for e in self.auto:
            out.extend(e.to_lines())
        out.append(AUTO_END)
        out.append(MANUAL_BEGIN)
        for e in self.manual:
            out.extend(e.to_lines())
        out.append(MANUAL_END)
        for ln in self.other:
            out.append(ln)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")

    def has_auto_entries(self) -> bool:
        return bool(self.auto)


def _hms(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds, 3))).split(".")[0]


def merge_adjacent(entries: list[EdlEntry], gap: float) -> list[EdlEntry]:
    """Merge entries whose gap is <= `gap` seconds."""
    if not entries:
        return []
    sorted_e = sorted(entries, key=lambda e: e.start)
    merged: list[EdlEntry] = [sorted_e[0]]
    for cur in sorted_e[1:]:
        prev = merged[-1]
        if cur.start - prev.end <= gap and cur.action == prev.action:
            joined_comment = prev.comment
            if cur.comment:
                joined_comment = (joined_comment + " | " + cur.comment).strip(" |")
            merged[-1] = EdlEntry(
                start=prev.start,
                end=max(prev.end, cur.end),
                action=prev.action,
                comment=joined_comment,
            )
        else:
            merged.append(cur)
    return merged


def entries_from_profanity_hits(hits, padding: float, action: int, merge_gap: float) -> list[EdlEntry]:
    """Convert ProfanityHit list -> merged EdlEntry list, padded for alignment slack."""
    raw = [
        EdlEntry(
            start=max(0.0, h.word.start - padding),
            end=h.word.end + padding,
            action=action,
            comment=f"Muted: {_hms(h.word.start)} to {_hms(h.word.end)} {h.context}",
        )
        for h in hits
    ]
    return merge_adjacent(raw, gap=merge_gap)
