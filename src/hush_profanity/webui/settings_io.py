"""Read + write the user's settings.toml from the Web UI.

We intentionally do NOT preserve comments on save — settings.example.toml is the
canonical documentation; settings.toml holds values. This keeps the writer
small (no third-party TOML round-trip dependency) and the on-disk format
predictable. If a fresh settings.toml is needed (first run after install), it
is created from the example.
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# Sections + keys that the Web UI knows how to edit. Anything outside this
# allow-list is preserved as-is on save (we re-merge it back into the dict).
EDITABLE_KEYS: dict[str, dict[str, type]] = {
    "library": {
        "roots": list,
        "extensions": list,
        "skip_if_processed": bool,
    },
    "whisper": {
        "model": str,
        "compute_type": str,
        "audio_language": str,
    },
    "alignment": {
        "enabled": bool,
    },
    "edl": {
        "profanity_action": int,
        "padding_seconds": float,
        "merge_gap_seconds": float,
    },
    "performance": {
        "gpu_workers": int,
    },
    "webui": {
        "port": int,
        "default_action": int,
    },
}

ALLOWED_VALUES: dict[tuple[str, str], list] = {
    ("whisper", "model"): [
        "tiny", "base", "small", "medium",
        "large-v2", "large-v3", "large-v3-turbo",
    ],
    ("whisper", "compute_type"): ["float16", "float32", "int8", "int8_float16"],
    ("edl", "profanity_action"): [0, 1],
    ("webui", "default_action"): [0, 1],
}


def read_full(settings_path: Path) -> dict:
    """Return the parsed contents of settings.toml as a plain dict.

    If settings.toml doesn't exist, returns the parsed example template
    instead (so first-run users get sensible defaults to edit).
    """
    if not settings_path.exists():
        example = settings_path.parent / "settings.example.toml"
        if not example.exists():
            return {}
        with open(example, "rb") as f:
            return tomllib.load(f)
    with open(settings_path, "rb") as f:
        return tomllib.load(f)


def validate_updates(updates: dict) -> list[str]:
    """Return a list of validation errors for the proposed updates dict.

    `updates` is the same shape as a parsed TOML: {section: {key: value, ...}}.
    Empty list means valid.
    """
    errors: list[str] = []
    for section, section_keys in updates.items():
        if section not in EDITABLE_KEYS:
            errors.append(f"unknown section [{section}]")
            continue
        for key, value in section_keys.items():
            allowed = EDITABLE_KEYS[section]
            if key not in allowed:
                errors.append(f"[{section}] key '{key}' is not editable from the Web UI")
                continue
            expected_type = allowed[key]
            if expected_type is list:
                if not isinstance(value, list):
                    errors.append(f"[{section}].{key} must be a list (got {type(value).__name__})")
                elif not all(isinstance(x, str) for x in value):
                    errors.append(f"[{section}].{key} must be a list of strings")
            elif expected_type is bool:
                if not isinstance(value, bool):
                    errors.append(f"[{section}].{key} must be true or false (got {type(value).__name__})")
            elif expected_type is int:
                if isinstance(value, bool) or not isinstance(value, int):
                    errors.append(f"[{section}].{key} must be an integer (got {type(value).__name__})")
            elif expected_type is float:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"[{section}].{key} must be a number (got {type(value).__name__})")
            elif expected_type is str:
                if not isinstance(value, str):
                    errors.append(f"[{section}].{key} must be a string (got {type(value).__name__})")
            allowed_values = ALLOWED_VALUES.get((section, key))
            if allowed_values is not None and value not in allowed_values:
                errors.append(
                    f"[{section}].{key} must be one of {allowed_values} (got {value!r})"
                )
    # Cross-key sanity: if roots is provided, every entry must be non-empty string.
    roots = updates.get("library", {}).get("roots")
    if roots is not None and any(not r.strip() for r in roots):
        errors.append("[library].roots must not contain empty strings")
    return errors


def write_full(settings_path: Path, data: dict) -> None:
    """Serialize `data` (parsed-TOML shape) back to settings.toml.

    Comments are not preserved. Section ordering matches our schema (deterministic).
    Atomic via tempfile rename.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump_toml(data)
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(settings_path)


def merge_updates(current: dict, updates: dict) -> dict:
    """Return a new dict that is `current` with `updates` overlaid section-by-section.

    Any section in `current` not present in `updates` is preserved as-is. Within an
    updated section, keys not in the update are preserved from current. So callers
    can post a partial update without wiping unrelated settings.
    """
    out: dict = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    for section, section_updates in updates.items():
        if section not in out or not isinstance(out[section], dict):
            out[section] = {}
        for key, value in section_updates.items():
            out[section][key] = value
    return out


# ----- minimal hand-rolled TOML writer (small/fixed schema, no comment support) -----

_PREFERRED_SECTION_ORDER = [
    "library", "whisper", "alignment", "edl", "subtitles",
    "paths", "webui", "performance",
]


def _dump_toml(data: dict) -> str:
    lines: list[str] = []
    section_keys = list(data.keys())
    section_keys.sort(key=lambda s: (
        _PREFERRED_SECTION_ORDER.index(s) if s in _PREFERRED_SECTION_ORDER else 999,
        s,
    ))
    for i, section in enumerate(section_keys):
        if i > 0:
            lines.append("")
        lines.append(f"[{section}]")
        section_data = data[section]
        if not isinstance(section_data, dict):
            continue
        for key, value in section_data.items():
            lines.append(f"{key} = {_format_value(value, key=key)}")
    return "\n".join(lines) + "\n"


def _format_value(value, key: str = "") -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        s = repr(value)
        if "." not in s and "e" not in s:
            s += ".0"
        return s
    if isinstance(value, str):
        return _format_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(x, str) for x in value):
            inner = ",\n".join(f"    {_format_string(x)}" for x in value)
            return f"[\n{inner},\n]"
        # Other list types — flat single-line
        return "[" + ", ".join(_format_value(x) for x in value) + "]"
    raise TypeError(f"Cannot TOML-serialize {key}: unsupported type {type(value).__name__}")


def _format_string(s: str) -> str:
    # TOML basic strings: backslash + double-quote escape, then double-quote wrap.
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
