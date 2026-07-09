"""tools.read_file_config — read_file tool config. Reads config/tools.toml ([read_file] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "read_file": {
        "default_max_chars": 8000,
        "min_max_chars": 100,
        "max_max_chars": 100_000,
        "hard_byte_cap": 2_000_000,
        "path_preview_chars": 120,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("read_file", _DEFAULTS["read_file"])
_rf = _DEFAULTS["read_file"]

READ_FILE_DEFAULT_MAX_CHARS: Final[int] = int(_cfg.get("default_max_chars", _rf["default_max_chars"]))
READ_FILE_MIN_MAX_CHARS: Final[int] = int(_cfg.get("min_max_chars", _rf["min_max_chars"]))
READ_FILE_MAX_MAX_CHARS: Final[int] = int(_cfg.get("max_max_chars", _rf["max_max_chars"]))
READ_FILE_HARD_BYTE_CAP: Final[int] = int(_cfg.get("hard_byte_cap", _rf["hard_byte_cap"]))
READ_FILE_PATH_PREVIEW_CHARS: Final[int] = int(_cfg.get("path_preview_chars", _rf["path_preview_chars"]))

__all__ = [
    "READ_FILE_DEFAULT_MAX_CHARS", "READ_FILE_MIN_MAX_CHARS", "READ_FILE_MAX_MAX_CHARS",
    "READ_FILE_HARD_BYTE_CAP", "READ_FILE_PATH_PREVIEW_CHARS",
]
