"""Config for the agent_coord_status skill (path of the agent-coord CLI, timeouts)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

__all__ = ["CLI_PATH", "TIMEOUT_SECONDS", "SUPERVISOR_INFO"]

_CFG = Path(__file__).resolve().parent.parent / "config" / "agent_coord_skill.toml"


def _load() -> dict:
    try:
        with open(_CFG, "rb") as f:
            return tomllib.load(f).get("agent_coord", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


_DATA = _load()
CLI_PATH: Final[str] = str(Path(_DATA.get("cli_path", "~/workspace/agent-coord/bin/agent-coord")).expanduser())
TIMEOUT_SECONDS: Final[float] = float(_DATA.get("timeout_seconds", 10))
SUPERVISOR_INFO: Final[str] = str(Path(_DATA.get("supervisor_info", "~/.cache/agent-coord/supervisor.json")).expanduser())
