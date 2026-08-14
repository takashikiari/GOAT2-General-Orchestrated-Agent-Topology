"""admin_panel.routes.memory — read-only L1/L2/L3 memory-browsing endpoints.

Each route calls straight through to the registry's existing tier objects
(permanent/working/episodic) — no new data-access logic. A backend being
down degrades only its own route, returned as {"error": ...} with HTTP 200
(this is a debug tool, not a critical path — a raw 500 would be misleading).
"""
from __future__ import annotations

import httpx
import redis.exceptions
from fastapi import APIRouter, Query, Request

from utils.logging.setup import get_logger

router = APIRouter()
log = get_logger(__name__)

_VALID_ORDERS = frozenset({"recent", "oldest"})


@router.get("/api/memory/facts")
async def get_facts(request: Request) -> dict:
    registry = request.app.state.registry
    try:
        facts = await registry.permanent_memory.get_all_facts()
    except httpx.HTTPError as exc:
        log.warning("get_facts: Letta unavailable: %s", exc)
        return {"error": "Letta unavailable"}
    return {"facts": facts}


@router.get("/api/memory/working/{chat_id}")
async def get_working(request: Request, chat_id: str) -> dict:
    registry = request.app.state.registry
    try:
        messages = await registry.working_memory.get_messages(chat_id)
    except redis.exceptions.RedisError as exc:
        log.warning("get_working: Redis unavailable: %s", exc)
        return {"error": "Redis unavailable"}
    return {"messages": messages}


@router.get("/api/memory/episodic/{chat_id}")
async def get_episodic(
    request: Request,
    chat_id: str,
    limit: int = Query(50, ge=1, le=500),
    order: str = "recent",
) -> dict:
    if order not in _VALID_ORDERS:
        return {"error": f"unknown order {order!r}; expected 'recent' or 'oldest'"}
    registry = request.app.state.registry
    try:
        if order == "oldest":
            entries = await registry.episodic_memory.get_oldest(limit, chat_id=chat_id)
        else:
            entries = await registry.episodic_memory.get_recent(chat_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 — ChromaDB has no single documented exception base
        log.warning("get_episodic: ChromaDB unavailable: %s", exc)
        return {"error": "ChromaDB unavailable"}
    return {"entries": entries}
