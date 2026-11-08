"""admin_panel.routes.chat — the panel's chat with GOAT (the same conversation as Telegram).

Read routes use the normal admin auth (applied by create_app); the routes that act (send, cancel) add the stricter
``require_admin_write_auth``. All logic lives in ``orchestrator.chat_service.ChatService``, which the bot builds and
puts on the registry, so the panel shares the bot's orchestrator and per-chat lock.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from admin_panel.telegram_auth import require_admin_write_auth
from orchestrator.chat_service import ChatBusy, ChatService, ChatUnavailable

router = APIRouter()

__all__ = ["router"]


class ChatIn(BaseModel):
    text: str


def _service(request: Request) -> ChatService:
    svc = getattr(request.app.state.registry, "chat_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="chat not available")
    return svc


@router.get("/api/chat/state")
async def chat_state(request: Request) -> dict:
    return _service(request).state()


@router.post("/api/chat", dependencies=[Depends(require_admin_write_auth)])
async def chat_send(request: Request, body: ChatIn) -> dict:
    try:
        turn = _service(request).submit(body.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ChatBusy:
        raise HTTPException(status_code=429, detail="too many messages waiting")
    except ChatUnavailable:
        raise HTTPException(status_code=503, detail="chat not available")
    return {"turn_id": turn.id}


@router.get("/api/chat/turns/{turn_id}")
async def chat_turn(request: Request, turn_id: str, after: int = 0) -> dict:
    view = _service(request).get(turn_id, after)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown turn")
    return view


@router.post("/api/chat/turns/{turn_id}/cancel", dependencies=[Depends(require_admin_write_auth)])
async def chat_cancel(request: Request, turn_id: str) -> dict:
    svc = _service(request)
    if svc.get(turn_id) is None:
        raise HTTPException(status_code=404, detail="unknown turn")
    return {"cancelled": svc.cancel(turn_id)}
