"""telegram_interface._plugin_scanner — background hot-reload for tool plugins.

Provides a PTB ``post_init`` hook that runs one immediate plugin scan, then a
30-second reconcile loop. The loop catches and logs per-iteration errors so a
single bad scan never kills the watcher. Started via the bot's ``post_init``
so the task lives and dies with the application.

Also schedules the (optional) admin panel server as a background task.
``admin_panel.server`` is imported lazily, inside ``_post_init``, and guarded:
the admin panel is a debug convenience, not a hard dependency of the bot's
core Telegram function, so a missing dependency (e.g. fastapi/uvicorn not
installed) or a bad ``config/admin_panel.toml`` must degrade to a logged
warning instead of preventing the bot from starting at all.
The ``admin_panel.tunnel`` (Cloudflare Quick Tunnel) is scheduled the same way,
for the same reason.

``post_init_hook`` returns a ``(post_init, post_shutdown)`` pair rather than
just the former: the plugin-scan loop and the admin panel/tunnel tasks are
started with a bare ``asyncio.create_task`` and never awaited anywhere, so
without an explicit cancel-and-await on shutdown, PTB's own graceful
shutdown finishes and closes the event loop while these tasks are still
mid-flight — they get destroyed abruptly instead of cancelled cleanly,
producing "Task was destroyed but it is pending!" plus a cascading
"RuntimeError: Event loop is closed" traceback from uvicorn's lifespan
handling, on every single bot restart. The returned ``post_shutdown``
cancels and awaits every task this module started, mirroring the pattern
``telegram_interface.bot``'s own ``drain_background`` already uses for the
orchestrator/memory-layers background work.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from plugins.plugins_config import PLUGIN_SCAN_INTERVAL_SECONDS as _SCAN_INTERVAL
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram import Application
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["post_init_hook"]


async def _loop(registry: "ServiceRegistry") -> None:
    """Immediate scan, then reconcile plugins every 30 s. Never raises."""
    try:
        registry.plugin_manager.scan()
    except Exception as exc:  # noqa: BLE001
        log.warning("initial plugin scan failed: %s", exc)
    while True:
        try:
            await asyncio.sleep(_SCAN_INTERVAL)
            registry.plugin_manager.scan()
        except Exception as exc:  # noqa: BLE001
            log.warning("plugin scan failed: %s", exc)


def post_init_hook(registry: "ServiceRegistry"):
    """Return ``(post_init, post_shutdown)`` PTB coroutines sharing this
    module's background tasks — start them on init, cancel them on shutdown.
    """
    tasks: list[asyncio.Task] = []
    admin_task: asyncio.Task | None = None
    # Single-slot holder for the uvicorn.Server instance, populated by
    # admin_panel.server.start()'s on_started callback once it exists —
    # needed so _post_shutdown can request graceful shutdown instead of
    # cancelling that task outright (see admin_panel/server.py:start).
    admin_server: list = []

    async def _post_init(application: "Application") -> None:
        nonlocal admin_task
        # Pre-warm ChromaDB first (serial: BM25 build reads from it).
        await registry.episodic_memory.warmup()
        # Then warm BM25 + GLiNER + CrossEncoder in parallel — all outside the
        # per-turn prefetch timeout so the first real request has a fully ready
        # retrieval stack. GLiNER must be here: boost_by_entities calls it inside
        # every cold/drift prefetch and its load (~1-3 s) would exceed the timeout.
        warmup_tasks: list[tuple[str, object]] = [
            ("BM25", registry.bm25_index.warmup()),
            ("GLiNER", registry.gliner_extractor.warmup()),
        ]
        if registry.reranker is not None:
            warmup_tasks.append(("CrossEncoder", registry.reranker.warmup()))
        results = await asyncio.gather(
            *(coro for _, coro in warmup_tasks), return_exceptions=True
        )
        for (name, _), result in zip(warmup_tasks, results):
            if isinstance(result, BaseException):
                log.error("warmup failed for %s: %s — first turn may be slow", name, result)
        tasks.append(asyncio.create_task(_loop(registry)))
        try:
            from admin_panel.server import start as _start_admin_panel
            admin_task = asyncio.create_task(
                _start_admin_panel(registry, on_started=admin_server.append)
            )
            tasks.append(admin_task)
        except Exception as exc:  # noqa: BLE001 — panel is optional, bot startup is not
            log.warning("admin panel unavailable: %s", exc)
        try:
            from admin_panel.tunnel import start as _start_admin_tunnel
            tasks.append(asyncio.create_task(_start_admin_tunnel(application)))
        except Exception as exc:  # noqa: BLE001 — tunnel is optional, bot startup is not
            log.warning("admin panel tunnel unavailable: %s", exc)

    async def _post_shutdown(application: "Application") -> None:
        """Cancel and await every task this module started, before the event
        loop closes. Never raises — a task that's already finished or whose
        cancellation itself errors must not block the rest of shutdown.

        The admin-panel task is deliberately excluded from the raw-cancel
        loop: it's asked to exit gracefully instead (``should_exit = True``),
        since a raw cancel there skips uvicorn's own shutdown path and leaks
        its internal lifespan task. It's still awaited below like the rest.
        """
        if admin_server:
            admin_server[0].should_exit = True
        for task in tasks:
            if task is admin_task:
                continue
            task.cancel()
        for task in tasks:
            try:
                await task
            # CancelledError is a BaseException (Python 3.8+), not caught by
            # `except Exception` — it's expected here since we just cancelled
            # every one of these tasks ourselves and want that to be quiet,
            # not propagate as a shutdown failure.
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    return _post_init, _post_shutdown