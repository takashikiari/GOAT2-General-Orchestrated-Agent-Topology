"""admin_panel.routes.agents — the admin chat's conversation split in two: what Laris and GOAT say (/api/chat/history)
and what other agent-coord sessions told GOAT, with GOAT's reply (/api/agents). Read-only."""
from __future__ import annotations

import redis.exceptions
from fastapi import APIRouter, Request

from admin_panel.coord_feed import split_conversation, split_tools
from config.admin_chat import load_admin_chat_id
from utils.logging.setup import get_logger

router = APIRouter()
log = get_logger(__name__)

__all__ = ["router"]


async def _stored(request: Request) -> tuple[str, list[dict], list[str]]:
    chat_id = load_admin_chat_id()
    if not chat_id:
        return "", [], []
    try:
        return chat_id, await request.app.state.registry.working_memory.get_messages(chat_id), []
    except redis.exceptions.RedisError as exc:
        log.warning("agents: Redis unavailable: %s", exc)
        return chat_id, [], ["Redis unavailable"]


def _with_warnings(body: dict, warnings: list[str]) -> dict:
    if warnings:
        body["warnings"] = warnings
    return body


@router.get("/api/chat/history")
async def chat_history(request: Request) -> dict:
    chat_id, stored, warnings = await _stored(request)
    chat, _ = split_conversation(stored)
    messages = []
    for m in chat:
        content = m.get("content") or ""
        tools = None
        if m.get("role") == "assistant":  # what Telegram showed: the reply, without GOAT's [Tool calls] evidence
            tools, content = split_tools(content)
        messages.append({"role": m.get("role", ""), "content": content, "tools": tools, "timestamp": m.get("timestamp", 0)})
    return _with_warnings({"chat_id": chat_id, "messages": messages}, warnings)


@router.get("/api/agents")
async def agents(request: Request) -> dict:
    chat_id, stored, warnings = await _stored(request)
    _, turns = split_conversation(stored)
    return _with_warnings({"chat_id": chat_id, "turns": turns}, warnings)
