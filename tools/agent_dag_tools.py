"""tools.agent_dag_tools — working-memory tools for DAG agents.

Agents use these to read and write context in the Redis working tier
(prefix ``wm:dag:``) during DAG task execution.  The handlers use
a lazy Redis connection drawn from the system's ``WORKING_STORAGE_URL``.

Constants exported: MEMORY_RECENT_DAG, MEMORY_GET_DAG,
MEMORY_STORE_DAG, MEMORY_SEARCH_DAG.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from tools.types import AgentTool

log = logging.getLogger("goat2.tools.agent_dag")

_PREFIX = "wm:dag"
_MAX_RECENT = 20
_CLIENT: Any = None  # lazy


def _get_redis():
    global _CLIENT
    if _CLIENT is None:
        import redis.asyncio as aioredis
        from memory.config import WORKING_STORAGE_URL
        _CLIENT = aioredis.from_url(WORKING_STORAGE_URL, decode_responses=True)
    return _CLIENT


# ── MEMORY_STORE_DAG ──────────────────────────────────────────────────────────

async def _memory_store(key: str, value: str, namespace: str = "global") -> str:
    r = _get_redis()
    full_key = f"{_PREFIX}:{namespace}:{key}"
    await r.set(full_key, value)
    log.debug("dag_mem store key=%s", full_key)
    return f"Stored '{key}' in namespace '{namespace}'."

MEMORY_STORE_DAG = AgentTool(
    name="memory_store",
    description=(
        "Store a string value in working memory under a key. "
        "Use namespace to group entries by task or topic (default: 'global')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key":       {"type": "string", "description": "Entry key"},
            "value":     {"type": "string", "description": "String value to store"},
            "namespace": {"type": "string", "description": "Logical group (default: 'global')"},
        },
        "required": ["key", "value"],
    },
    handler=_memory_store,
)


# ── MEMORY_GET_DAG ────────────────────────────────────────────────────────────

async def _memory_get(key: str, namespace: str = "global") -> str:
    r = _get_redis()
    full_key = f"{_PREFIX}:{namespace}:{key}"
    val = await r.get(full_key)
    if val is None:
        return f"No entry found for key '{key}' in namespace '{namespace}'."
    log.debug("dag_mem get key=%s", full_key)
    return val

MEMORY_GET_DAG = AgentTool(
    name="memory_get",
    description="Retrieve a specific working memory entry by key and optional namespace.",
    parameters={
        "type": "object",
        "properties": {
            "key":       {"type": "string"},
            "namespace": {"type": "string", "description": "Namespace (default: 'global')"},
        },
        "required": ["key"],
    },
    handler=_memory_get,
)


# ── MEMORY_RECENT_DAG ─────────────────────────────────────────────────────────

async def _memory_recent(namespace: str = "global", n: int = 5) -> str:
    r = _get_redis()
    pattern = f"{_PREFIX}:{namespace}:*"
    keys = await r.keys(pattern)
    if not keys:
        return f"No entries in namespace '{namespace}'."
    keys_sorted = sorted(keys)[: int(n)]
    values = await r.mget(*keys_sorted)
    lines: list[str] = []
    for k, v in zip(keys_sorted, values):
        short_key = k.split(":", 2)[-1] if ":" in k else k
        lines.append(f"{short_key}: {(v or '')[:200]}")
    log.debug("dag_mem recent namespace=%s n=%d results=%d", namespace, n, len(lines))
    return "\n".join(lines)

MEMORY_RECENT_DAG = AgentTool(
    name="memory_recent",
    description=(
        "Retrieve the most recent working memory entries in a namespace. "
        "Returns up to n entries (default 5)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "Namespace to list (default: 'global')"},
            "n":         {"type": "integer", "description": "Max entries to return (default 5)"},
        },
        "required": [],
    },
    handler=_memory_recent,
)


# ── MEMORY_SEARCH_DAG ─────────────────────────────────────────────────────────

async def _memory_search(query: str, namespace: str = "global") -> str:
    r = _get_redis()
    pattern = f"{_PREFIX}:{namespace}:*"
    keys = await r.keys(pattern)
    if not keys:
        return f"No entries in namespace '{namespace}'."
    values = await r.mget(*keys)
    q = query.lower()
    results: list[str] = []
    for k, v in zip(keys, values):
        text = (v or "").lower()
        if q in text or q in k.lower():
            short_key = k.split(":", 2)[-1] if ":" in k else k
            results.append(f"{short_key}: {(v or '')[:300]}")
    log.debug("dag_mem search query=%r namespace=%s results=%d", query, namespace, len(results))
    return "\n".join(results[:20]) if results else f"No matches for '{query}' in namespace '{namespace}'."

MEMORY_SEARCH_DAG = AgentTool(
    name="memory_search",
    description="Search working memory entries in a namespace by substring query.",
    parameters={
        "type": "object",
        "properties": {
            "query":     {"type": "string", "description": "Search query"},
            "namespace": {"type": "string", "description": "Namespace to search (default: 'global')"},
        },
        "required": ["query"],
    },
    handler=_memory_search,
)
