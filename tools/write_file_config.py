"""tools.write_file_config — write_file tool config. Reads config/tools.toml ([write_file] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "write_file": {
        "max_content_chars": 200_000,
        "path_preview_chars": 120,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("write_file", _DEFAULTS["write_file"])
_wf = _DEFAULTS["write_file"]

WRITE_FILE_MAX_CONTENT_CHARS: Final[int] = int(_cfg.get("max_content_chars", _wf["max_content_chars"]))
WRITE_FILE_PATH_PREVIEW_CHARS: Final[int] = int(_cfg.get("path_preview_chars", _wf["path_preview_chars"]))

__all__ = ["WRITE_FILE_MAX_CONTENT_CHARS", "WRITE_FILE_PATH_PREVIEW_CHARS"]
