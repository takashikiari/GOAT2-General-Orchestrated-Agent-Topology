"""Load config/goat.toml and expose typed section accessors. tomllib is stdlib in Python ≥3.11."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Final

log = logging.getLogger("goat2.config.toml_loader")

__all__ = ["TomlConfig", "load_toml"]

_PATH: Final[Path] = Path(__file__).parent / "goat.toml"


def _load_raw() -> dict[str, Any]:
    """Read goat.toml; returns {} when absent or when no toml parser is available."""
    if not _PATH.exists():
        log.debug("toml_loader: %s not found; returning empty config", _PATH)
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
            with _PATH.open("rb") as f:
                data = tomllib.load(f)
        else:
            import tomli  # type: ignore[import]
            with _PATH.open("rb") as f:
                data = tomli.load(f)
        log.debug("toml_loader: %s loaded (top-level keys=%s)", _PATH, list(data))
        return data
    except Exception as exc:
        log.debug("toml_loader: %s parse error: %s; returning empty config", _PATH, exc)
        return {}


class TomlConfig:
    """Typed read-only view over goat.toml sections. Missing keys return the given default."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._model    = raw.get("model",    {})
        self._agents   = raw.get("agents",   {})
        self._keys     = raw.get("api_keys", {})
        self._memory   = raw.get("memory",   {})
        self._channels = raw.get("channels", {})

    def model(self, key: str, default: str = "") -> str:
        """Return a value from [model]; empty string when key is absent."""
        return str(self._model.get(key) or default)

    def agent(self, role: str) -> str:
        """Return the model key for an agent role; empty string when absent."""
        return str(self._agents.get(role) or "")

    def api_key(self, provider: str) -> str:
        """Return an API key from [api_keys]; empty string when absent or blank."""
        return str(self._keys.get(provider) or "")

    def memory_str(self, key: str, default: str = "") -> str:
        """Return a string value from [memory]."""
        return str(self._memory.get(key) or default)

    def memory_int(self, key: str, default: int = 0) -> int:
        """Return an integer value from [memory]."""
        v = self._memory.get(key)
        return int(v) if v is not None else default

    def channel_str(self, key: str, default: str = "") -> str:
        """Return a string value from [channels]."""
        return str(self._channels.get(key) or default)

    def channel_bool(self, key: str, default: bool = False) -> bool:
        """Return a boolean value from [channels]."""
        v = self._channels.get(key)
        return bool(v) if v is not None else default


def load_toml() -> TomlConfig:
    """Load goat.toml and return a TomlConfig; returns an empty config on any error."""
    return TomlConfig(_load_raw())
