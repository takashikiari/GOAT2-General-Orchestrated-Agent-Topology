"""admin_panel.server — FastAPI app + embedded uvicorn server for the GOAT 2.0 admin panel.

Runs read-only HTTP routes over the live ServiceRegistry, started as a
background asyncio task from the bot's post_init hook (same event loop, same
registry instance — metrics reflect the bot's real live state, and Redis/
Chroma/Letta clients stay bound to the one loop they were created on).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.routes import conversations, logs, memory, metrics
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["create_app", "start"]


def create_app(registry: "ServiceRegistry") -> FastAPI:
    """Build the FastAPI app, wiring ``registry`` into app.state for every route."""
    app = FastAPI(title="GOAT 2.0 Admin Panel")
    app.state.registry = registry
    app.include_router(metrics.router)
    app.include_router(logs.router)
    app.include_router(memory.router)
    app.include_router(conversations.router)
    return app


async def start(registry: "ServiceRegistry") -> None:
    """Start the admin server in the current event loop. Never raises.

    Calls ``Server._serve()`` directly instead of the public ``serve()`` —
    ``serve()`` unconditionally installs its own SIGINT/SIGTERM handlers via
    ``capture_signals()``, which would clobber python-telegram-bot's own
    shutdown handling since both run in the same process's main thread.
    ``_serve()`` is the same coroutine minus that signal capture.
    """
    try:
        app = create_app(registry)
        config = uvicorn.Config(app, host=ADMIN_HOST, port=ADMIN_PORT, log_level="warning")
        server = uvicorn.Server(config)
        await server._serve()
    except Exception as exc:  # noqa: BLE001 — must never take the bot down with it
        log.warning("admin panel server failed to start on %s:%d: %s", ADMIN_HOST, ADMIN_PORT, exc)
