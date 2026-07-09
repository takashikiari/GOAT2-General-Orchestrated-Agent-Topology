"""telegram_interface._plugin_scanner — background hot-reload for tool plugins.

Provides a PTB ``post_init`` hook that runs one immediate plugin scan, then a
30-second reconcile loop. The loop catches and logs per-iteration errors so a
single bad scan never kills the watcher. Started via the bot's ``post_init``
so the task lives and dies with the application.
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
    """Return a PTB ``post_init`` coroutine that warms memory and starts the scanner."""
    async def _post_init(application: "Application") -> None:
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
        asyncio.create_task(_loop(registry))
    return _post_init