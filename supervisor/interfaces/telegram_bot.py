"""GOAT 2.0 Telegram interface — one message = one intent.

The bot is a thin adapter: it converts incoming text to a GOAT intent,
runs it through the supervisor, and sends the result.summary back to
the chat. DSML stripping, tool-call filtering, and the GOAT decision
all happen inside the supervisor / goat_call pipeline — the bot
does not parse, reformat, or retry.

GRACEFUL SHUTDOWN:
    On SIGTERM / SIGINT, the application stops polling; the
    ``_shutdown()`` coroutine then runs in the SAME event loop as
    the polling, calling each supervisor's ``finalize_session()``
    which in turn stops the MemoryDaemon and ToolsWatcher with a
    bounded 5 s wait. This prevents the
    "Task was destroyed but it is pending!" warning that
    previously appeared when the event loop closed while the
    background daemon and watcher tasks were still pending.
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

# How long the shutdown path will wait on any single supervisor's
# finalize_session() (which itself bounds the daemon + watcher
# stops at 5 s each). Total worst-case shutdown ≈ N * 6 s.
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
    """Forward incoming text to the per-chat supervisor and reply with the summary.

    Non-text updates (stickers, photos, voice notes, etc.) are silently
    ignored — they have no user intent to forward.
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


async def _finalize_all_sessions() -> None:
    """End-of-process: persist behavior profiles and stop memory daemons.

    Runs in the SAME event loop as the polling, so each
    ``finalize_session()`` can actually await the MemoryDaemon and
    ToolsWatcher stop coroutines. Previously this ran in a fresh
    ``asyncio.run()`` from the main thread, which left the daemon
    / watcher tasks bound to the (now-closed) polling loop and
    produced "Task was destroyed but it is pending!" warnings.
    """
    for chat_id, supervisor in list(_sessions.items()):
        try:
            # Bounded so one slow finalizer cannot block the whole
            # shutdown — the daemon / watcher have their own 5 s
            # budgets internally, so 10 s here is a comfortable cap.
            await asyncio.wait_for(
                supervisor.finalize_session(),
                timeout=_SHUTDOWN_WAIT_TIMEOUT_S,
            )
            log.info("finalize_session: chat=%d done", chat_id)
        except asyncio.TimeoutError:
            log.warning(
                "finalize_session: chat=%d timed out after %.1fs — daemon/watcher may be discarded",
                chat_id, _SHUTDOWN_WAIT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort shutdown
            log.warning("finalize_session: chat=%d failed: %s", chat_id, exc)


async def _run_with_shutdown() -> None:
    """Run the polling loop, then finalize — both in the same event loop.

    ``app.run_polling()`` blocks until SIGINT / SIGTERM; after it
    returns, the loop is still alive so we can call the supervisor
    ``finalize_session()`` (which awaits the daemon + watcher
    stops) without leaving tasks pending.
    """
    app = build_app()
    log.info("GOAT 2.0 Telegram bot starting.")
    try:
        await app.run_polling(allowed_updates=["message"])
    finally:
        log.info("GOAT 2.0 Telegram bot shutting down — finalizing sessions")
        await _finalize_all_sessions()
        log.info("GOAT 2.0 Telegram bot shutdown complete")


def build_app() -> Application:
    """Build and configure the Telegram Application with one text handler."""
    if not _TOKEN:
        raise RuntimeError("channels.telegram_token is not set in config/goat.toml")
    app = Application.builder().token(_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app


def main() -> None:
    """Start the Telegram bot. SIGINT / SIGTERM trigger graceful shutdown.

    Both the polling and the finalization run inside a single
    ``asyncio.run()`` so the MemoryDaemon and ToolsWatcher tasks
    (created on the first supervisor's first run) are awaited and
    cancelled in the same event loop they were created in.
    """
    asyncio.run(_run_with_shutdown())


# Programmatic entry point — used by tests and by callers that want to start
# the bot without going through `python -m telegram_bot`. Alias of main().
run_polling = main


if __name__ == "__main__":
    main()
