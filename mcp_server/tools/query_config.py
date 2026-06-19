"""Configuration query tool — merge all ``config/*.toml`` files
into one structured dict, organized by file.

READ-ONLY: the tool only calls ``tomllib.load`` on existing
files. It never writes. Safe to run concurrently with
anything else.

USAGE:
    from mcp_server.tools.query_config import get_all_config, register
    cfg = get_all_config()
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("goat2.mcp_server.tools.query_config")

__all__ = ["get_all_config", "register"]


# All toml files that contribute to GOAT's runtime config.
# Listed explicitly (not globbed) so the MCP output is
# deterministic — adding a new toml requires editing this
# list, which is the intended review hook.
_CONFIG_FILES: tuple[str, ...] = (
    "goat.toml",
    "memory.toml",
    "dag.toml",
    "behavioral.toml",
    "tools.toml",
)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CONFIG_DIR: Path = _REPO_ROOT / "config"


def _load_one(path: Path) -> dict[str, Any]:
    """Load a single toml file. Returns ``{}`` on any failure.

    Uses ``tomllib`` (Python 3.11+) directly, without going
    through ``config.modular_loader`` — the MCP tool is a
    generic reader, not a config consumer. We don't want to
    trigger any of the side effects that the loader may have
    in the future.
    """
    if not path.exists():
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[import]
        with path.open("rb") as fh:
            return tomllib.load(fh)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        log.warning("query_config: %s load failed: %s", path, exc)
        return {"_error": f"{type(exc).__name__}: {exc}"}


def get_all_config() -> dict[str, Any]:
    """Read every config file and return ``{filename: sections}``.

    Returns:
        A dict with one key per toml file (``goat.toml``,
        ``memory.toml``, ...). Each value is the parsed
        toml as a nested dict (sections → keys → values).
        A file that fails to parse contributes
        ``{"_error": "..."}`` so the caller still sees
        something useful.

        The output also includes a top-level ``_meta`` key
        with the path of the config dir and a per-file
        ``loaded`` boolean, so the MCP client can quickly
        see which files were found.
    """
    out: dict[str, Any] = {}
    for name in _CONFIG_FILES:
        path = _CONFIG_DIR / name
        out[name] = _load_one(path)
    out["_meta"] = {
        "config_dir": str(_CONFIG_DIR),
        "files": {
            name: (out[name] != {} and "_error" not in out[name])
            for name in _CONFIG_FILES
        },
    }
    return out


# ── MCP wiring ────────────────────────────────────────────────

def register(server) -> None:
    """Register the config tool on an MCP ``Server``."""
    @server.tool(
        name="get_all_config",
        description=(
            "Read and merge all config/*.toml files into one dict, organized by filename. "
            "Each section becomes a nested key. Missing or unparseable files show up as "
            "{filename: {_error: '...'}}. READ-ONLY."
        ),
    )
    async def _get_all_config() -> dict[str, Any]:
        return get_all_config()