"""workflow.config — configuration for the DAG workflow engine.

Reads from the ``[workflow]`` section of ``config/memory.toml`` with
sensible defaults.  All values are immutable after construction.
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

_TOML_PATH = Path(__file__).parent.parent / "config" / "memory.toml"

_DEFAULTS: dict = {
    "dag_key_prefix": "dag",
    "dag_ttl_seconds": 3600,
    "max_concurrent_nodes": 8,
    "node_timeout_seconds": 300.0,
}


def _load_section() -> dict:
    try:
        with _TOML_PATH.open("rb") as fh:
            data = tomllib.load(fh)
        return data.get("workflow", {})
    except FileNotFoundError:
        return {}


@dataclasses.dataclass(frozen=True)
class WorkflowConfig:
    """Immutable configuration for the workflow subsystem.

    Args:
        redis_url: Redis connection string (same server as L2 working memory).
        dag_key_prefix: Key namespace prefix for DAG state in Redis.
        dag_ttl_seconds: TTL applied to all DAG Redis keys.
        max_concurrent_nodes: Max nodes running concurrently within one DAG.
        node_timeout_seconds: Per-node execution timeout in seconds.
    """

    redis_url: str
    dag_key_prefix: str = _DEFAULTS["dag_key_prefix"]
    dag_ttl_seconds: int = _DEFAULTS["dag_ttl_seconds"]
    max_concurrent_nodes: int = _DEFAULTS["max_concurrent_nodes"]
    node_timeout_seconds: float = _DEFAULTS["node_timeout_seconds"]

    @classmethod
    def from_toml(cls, redis_url: str) -> "WorkflowConfig":
        """Build a WorkflowConfig from ``config/memory.toml`` ``[workflow]`` section."""
        section = _load_section()
        return cls(
            redis_url=redis_url,
            dag_key_prefix=str(section.get("dag_key_prefix", _DEFAULTS["dag_key_prefix"])),
            dag_ttl_seconds=int(section.get("dag_ttl_seconds", _DEFAULTS["dag_ttl_seconds"])),
            max_concurrent_nodes=int(
                section.get("max_concurrent_nodes", _DEFAULTS["max_concurrent_nodes"])
            ),
            node_timeout_seconds=float(
                section.get("node_timeout_seconds", _DEFAULTS["node_timeout_seconds"])
            ),
        )
