"""admin_panel.routes.conversations — read-only chat_id discovery + per-chat timeline.

Chat discovery unions two independent sources: active sessions in Redis (L2,
bounded by WORKING_TTL_SECONDS) and chats that only survive in ChromaDB (L3,
past their L2 TTL). Either source failing degrades to a `warnings` list on
the response rather than a single {"error": ...} — these two routes
intentionally still return whatever half succeeded, unlike memory.py's
single-tier routes.
"""
from __future__ import annotations

import redis.exceptions
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/conversations")
async def list_conversations(request: Request) -> dict:
    registry = request.app.state.registry
    warnings: list[str] = []
    active: list[str] = []
    all_episodic: list[str] = []
    try:
        active = await registry.working_memory.list_chat_ids()
    except redis.exceptions.RedisError as exc:
        warnings.append(f"Redis unavailable: {exc}")
    try:
        all_episodic = await registry.episodic_memory.list_chat_ids()
    except Exception as exc:  # noqa: BLE001 — ChromaDB has no single documented exception base
        warnings.append(f"ChromaDB unavailable: {exc}")
    archived = sorted(set(all_episodic) - set(active))
    conversations = (
        [{"chat_id": c, "status": "active"} for c in sorted(active)]
        + [{"chat_id": c, "status": "archived_only"} for c in archived]
    )
    result: dict = {"conversations": conversations}
    if warnings:
        result["warnings"] = warnings
    return result


@router.get("/api/conversations/{chat_id}")
async def get_conversation(request: Request, chat_id: str) -> dict:
    registry = request.app.state.registry
    warnings: list[str] = []
    l2: list[dict] = []
    l3: list[dict] = []
    try:
        l2 = await registry.working_memory.get_messages(chat_id)
    except redis.exceptions.RedisError as exc:
        warnings.append(f"Redis unavailable: {exc}")
    try:
        l3 = await registry.episodic_memory.get_recent(chat_id, limit=100)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ChromaDB unavailable: {exc}")
    timeline = [
        {"tier": "L2", "timestamp": m.get("timestamp", 0), "role": m.get("role", ""), "content": m.get("content", "")}
        for m in l2
    ] + [
        {"tier": "L3", "timestamp": e["metadata"].get("timestamp", 0), "role": "", "content": e["content"]}
        for e in l3
    ]
    timeline.sort(key=lambda entry: float(entry["timestamp"] or 0))
    result: dict = {"chat_id": chat_id, "timeline": timeline}
    if warnings:
        result["warnings"] = warnings
    return result
