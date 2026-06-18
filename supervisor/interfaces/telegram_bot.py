"""GOAT 2.0 Telegram interface — one message = one intent.

Thin adapter: text → ``supervisor.run(intent)`` → ``reply_text(summary)``.
DSML stripping, tool calls, GOAT decision all happen inside the
supervisor / goat_call pipeline — the bot does not parse or retry.

GRACEFUL SHUTDOWN:
    ``Application.run_polling()`` manages its OWN event loop and
    handles SIGINT / SIGTERM via ``stop_signals``. We register
    ``_finalize_all_sessions`` as the ``post_shutdown`` callback so
    the per-supervisor finalization (which awaits the MemoryDaemon
    and ToolsWatcher stops, bounded at 5 s each) runs INSIDE the
    polling loop's event loop — the same loop the daemon / watcher
    tasks were created in. Earlier versions wrapped the polling
    call in ``asyncio.run()``, which created a nested event loop
    and raised ``RuntimeError: This event loop is already running``.
"""
from __future__ import annotations

import asyncio
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)s  %(message)s",
)
import os
from typing import Final

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config.registry import ServiceRegistry
from config.toml_loader import load_toml
from supervisor.interfaces.content_filter import mask_sensitive
from supervisor.supervisor import GoatSupervisor
from tools.registry_accessor import set_registry

log = logging.getLogger("goat2.supervisor.interfaces")

_TOKEN: Final[str] = os.environ.get("TELEGRAM_TOKEN") or load_toml().channel_str("telegram_token")
_NO_REPLY = "..."  # sent when the supervisor returns an empty summary

_registry = ServiceRegistry()
set_registry(_registry)
_sessions: dict[int, GoatSupervisor] = {}

# Outer cap per supervisor's ``finalize_session()`` (which itself
# bounds the daemon + watcher stops at 5 s each). Total worst-case
# shutdown ≈ N * 11 s.
_SHUTDOWN_WAIT_TIMEOUT_S: float = 10.0


def _supervisor_for(chat_id: int) -> GoatSupervisor:
    """Return the per-chat GoatSupervisor, creating it on first use."""
    if chat_id not in _sessions:
        _sessions[chat_id] = GoatSupervisor(registry=_registry)
    return _sessions[chat_id]


def _reply_text(text: str) -> str:
    """Final guard: never send an empty or whitespace-only message."""
    cleaned = (text or "").strip()
    return cleaned or _NO_REPLY


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward incoming text to the per-chat supervisor and reply.

    Non-text updates are silently ignored.
    """
    message = update.message
    if message is None or not message.text:
        log.debug("Ignoring non-text or empty message")
        return
    intent = message.text.strip()
    if not intent:
        return

    supervisor = _supervisor_for(message.chat_id)
    log.debug("chat=%d processing intent=%.80s", message.chat_id, intent)
    try:
        result = await supervisor.run(intent)
        await message.reply_text(_reply_text(mask_sensitive(result.summary)))
    except Exception as exc:
        log.exception("chat=%d error processing intent", message.chat_id)
        await message.reply_text(f"[error] {exc}")


async def _finalize_all_sessions(_app: Application) -> None:
    """End-of-process: persist profiles + stop daemons.

    Registered as ``Application.post_shutdown`` so it runs INSIDE
    the polling loop's event loop — the same loop the
    MemoryDaemon and ToolsWatcher tasks were created in. That is
    the only way ``await supervisor.finalize_session()`` can
    actually cancel those tasks before the loop closes.

    The ``_app`` arg is required by the post_shutdown signature; we
    ignore it. Each per-supervisor call is bounded by
    ``_SHUTDOWN_WAIT_TIMEOUT_S``; the daemon / watcher have their
    own 5 s budgets internally.
    """
    for chat_id, supervisor in list(_sessions.items()):
        try:
            await asyncio.wait_for(
                supervisor.finalize_session(),
                timeout=_SHUTDOWN_WAIT_TIMEOUT_S,
            )
            log.info("finalize_session: chat=%d done", chat_id)
        except asyncio.TimeoutError:
            log.warning(
                "finalize_session: chat=%d timed out after %.1fs",
                chat_id, _SHUTDOWN_WAIT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort shutdown
            log.warning("finalize_session: chat=%d failed: %s", chat_id, exc)


def build_app() -> Application:
    """Build the Telegram Application; wire the post_shutdown hook."""
    if not _TOKEN:
        raise RuntimeError("channels.telegram_token is not set in config/goat.toml")
    app = (
        Application.builder()
        .token(_TOKEN)
        .post_shutdown(_finalize_all_sessions)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app


def main() -> None:
    """Start the bot — SIGINT / SIGTERM trigger graceful shutdown.

    ``Application.run_polling()`` runs the polling loop in its own
    event loop and registers signal handlers internally. The
    ``post_shutdown`` hook (see ``build_app``) is awaited by the
    same loop before it tears down, so it can cancel the
    MemoryDaemon and ToolsWatcher tasks cleanly.
    """
    log.info("GOAT 2.0 Telegram bot starting.")
    try:
        build_app().run_polling(allowed_updates=["message"])
    finally:
        log.info("GOAT 2.0 Telegram bot shutdown complete")


# Programmatic entry point — alias of main() for tests and external callers.
run_polling = main


if __name__ == "__main__":
    main()
