"""admin_panel.server — FastAPI app + embedded uvicorn server for the GOAT 2.0 admin panel.

Runs read-only HTTP routes over the live ServiceRegistry, started as a
background asyncio task from the bot's post_init hook (same event loop, same
registry instance — metrics reflect the bot's real live state, and Redis/
Chroma/Letta clients stay bound to the one loop they were created on).

Every /api/* route requires require_admin_auth (Telegram initData, restricted
to admin_chat_id) — this matters once [tunnel] enabled makes the panel
reachable over the internet, not just from localhost. The index page is
exempt: it carries no sensitive data, only the JS that will itself attach
initData to its own API calls.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.routes import conversations, index, logs, memory, metrics
from admin_panel.telegram_auth import require_admin_auth
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

_STATIC_ASSETS_DIR = Path(__file__).parent / "static" / "assets"

log = get_logger(__name__)
__all__ = ["create_app", "start"]


def create_app(registry: "ServiceRegistry") -> FastAPI:
    """Build the FastAPI app, wiring ``registry`` into app.state for every route."""
    # docs_url/redoc_url/openapi_url disabled: once [tunnel] enabled proxies the
    # whole app (not just /api/*), FastAPI's auto-generated docs endpoints would
    # otherwise be unauthenticated and internet-reachable.
    app = FastAPI(title="GOAT 2.0 Admin Panel", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry = registry
    app.include_router(index.router)
    # check_dir=False: a missing build (e.g. before the first `npm run build`)
    # must degrade to 404s on /assets/*, not crash create_app and take the
    # whole panel (including / and /api/*) down with it.
    app.mount("/assets", StaticFiles(directory=_STATIC_ASSETS_DIR, check_dir=False), name="assets")
    auth = [Depends(require_admin_auth)]
    app.include_router(metrics.router, dependencies=auth)
    app.include_router(logs.router, dependencies=auth)
    app.include_router(memory.router, dependencies=auth)
    app.include_router(conversations.router, dependencies=auth)
    return app


async def start(
    registry: "ServiceRegistry",
    *,
    on_started: "Callable[[uvicorn.Server], None] | None" = None,
) -> None:
    """Start the admin server in the current event loop. Never raises.

    Calls ``Server._serve()`` directly instead of the public ``serve()`` —
    ``serve()`` unconditionally installs its own SIGINT/SIGTERM handlers via
    ``capture_signals()``, which would clobber python-telegram-bot's own
    shutdown handling since both run in the same process's main thread.
    ``_serve()`` is the same coroutine minus that signal capture.

    ``on_started``, if given, is called with the ``uvicorn.Server`` instance
    right after it's constructed — this is how a caller gets a handle to
    request graceful shutdown (``server.should_exit = True``) instead of
    cancelling this coroutine outright. Raw cancellation lands mid
    ``main_loop()`` and skips ``_serve()``'s own ``await self.shutdown()``
    call, which is what tears down uvicorn's internal lifespan task cleanly
    — cancelling from outside always leaked that task as a "Task was
    destroyed but it is pending!" error on every bot restart.

    Catches ``SystemExit`` in addition to ``Exception``: uvicorn's
    ``Server.startup()`` does not raise ``OSError`` on a bind failure (e.g.
    port already in use) — it catches that internally and calls
    ``sys.exit(1)`` instead. ``SystemExit`` derives from ``BaseException``,
    so it would otherwise skip a plain ``except Exception`` and propagate up
    through python-telegram-bot's own top-level ``(KeyboardInterrupt,
    SystemExit)`` handler, silently shutting down the entire bot. We
    deliberately do NOT catch bare ``BaseException`` here, since that would
    also swallow ``asyncio.CancelledError``, which PTB's own shutdown relies
    on being able to propagate through tasks it cancels.
    """
    try:
        app = create_app(registry)
        config = uvicorn.Config(app, host=ADMIN_HOST, port=ADMIN_PORT, log_level="warning")
        server = uvicorn.Server(config)
        if on_started is not None:
            on_started(server)
        await server._serve()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — SystemExit: uvicorn's startup() sys.exit(1)s on bind failure; must never take the bot down with it
        log.warning("admin panel server stopped (%s:%d): %s", ADMIN_HOST, ADMIN_PORT, exc)
