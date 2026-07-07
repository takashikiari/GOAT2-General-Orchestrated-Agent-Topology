"""telegram_interface._plugin_scanner — background hot-reload for tool plugins.

Provides a PTB ``post_init`` hook that runs one immediate plugin scan, then a
30-second reconcile loop. The loop catches and logs per-iteration errors so a
single bad scan never kills the watcher. Started via the bot's ``post_init``
so the task lives and dies with the application.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram import Application
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["post_init_hook"]

_SCAN_INTERVAL = 30


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
    """Return a PTB ``post_init`` coroutine that warms memory and starts the scanner."""
    async def _post_init(application: "Application") -> None:
        # Pre-warm ChromaDB, then build BM25 index and load reranker model in
        # parallel — all outside the per-turn prefetch timeout so the first real
        # request after restart has a fully ready retrieval stack.
        await registry.episodic_memory.warmup()
        warmup_tasks = [registry.bm25_index.warmup()]
        if registry.reranker is not None:
            warmup_tasks.append(registry.reranker.warmup())
        await asyncio.gather(*warmup_tasks, return_exceptions=True)
        asyncio.create_task(_loop(registry))
    return _post_init