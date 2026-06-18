"""Telegram interface — simple adapter that wires the
``GoatSupervisor`` to a Telegram chat.

USAGE:
    from supervisor.interfaces.telegram_bot import run_polling

    run_polling()             # blocks; uses TELEGRAM_BOT_TOKEN env

Or for programmatic use:
    from supervisor.interfaces.telegram_bot import handle_update

    await handle_update(update, context)

DESIGN:
  - No regex (per the supervisor rules).
  - No DSML processing here — ``pipeline.goat_call`` already
    strips DSML markers via the canonical ``utils.dsml`` module.
  - Replies are truncated to ``max_message_chars`` from
    ``config/goat.toml [telegram]`` (Telegram API limit).
  - ``basicConfig`` for logging at startup so the bot is
    runnable as a standalone entry point.
  - Graceful shutdown via ``Application.post_shutdown`` hook.

DEPENDENCIES:
  - ``python-telegram-bot`` v20+.
  - The ``ServiceRegistry`` is constructed lazily on the first
    update so importing this module does not require a working
    Redis / ChromaDB / Letta stack.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

log = logging.getLogger("goat2.supervisor.interfaces.telegram_bot")

__all__ = ["run_polling", "handle_update", "MAX_MESSAGE_CHARS_FALLBACK"]


# Fallback only — the real value comes from config/goat.toml
# [telegram]. Telegram's hard API limit is 4096; the config
# value should match.
MAX_MESSAGE_CHARS_FALLBACK: Final[int] = 4096


def _load_telegram_config() -> dict[str, int]:
    """Read [telegram] from config/goat.toml with safe defaults.

    Returns a flat dict with the three knobs the bot reads.
    Missing file or section → defensive defaults.
    """
    out: dict[str, int] = {
        "max_message_chars":   MAX_MESSAGE_CHARS_FALLBACK,
        "read_timeout_seconds": 30,
        "turn_warn_seconds":    20,
    }
    try:
        from config.modular_loader import _load_raw  # type: ignore
        data = _load_raw("goat.toml") or {}
        section = data.get("telegram", {}) or {}
        if isinstance(section, dict):
            for k in out:
                raw = section.get(k)
                if raw is None:
                    continue
                try:
                    out[k] = int(raw)
                except (TypeError, ValueError):
                    log.debug("telegram.%s=%r not int — using default", k, raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("telegram config load failed: %s", exc)
    return out


_CFG: Final[dict[str, int]] = _load_telegram_config()


def _truncate(text: str) -> str:
    """Truncate ``text`` to the configured Telegram max, with a marker."""
    cap = _CFG["max_message_chars"]
    if not text or len(text) <= cap:
        return text or ""
    return text[: max(0, cap - 16)] + "\n[…truncated…]"


def _split_message(text: str) -> list[str]:
    """Split ``text`` into chunks that each fit the Telegram cap."""
    cap = _CFG["max_message_chars"]
    if not text:
        return [""]
    if len(text) <= cap:
        return [text]
    chunks: list[str] = []
    step = max(1, cap)
    for i in range(0, len(text), step):
        chunks.append(text[i:i + step])
    return chunks


async def handle_update(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
) -> None:
    """Handle one Telegram ``Update``: build a SupervisorResult and reply.

    Defensive: any exception is logged and a clarification
    fallback is sent to the user. Never raises.

    Args:
        update: A ``telegram.Update`` (text or command).
        context: PTB callback context (provides ``bot`` and
            per-user ``context.user_data``).
    """
    try:
        message = update.effective_message
        if message is None or message.text is None:
            return
        intent = (message.text or "").strip()
        if not intent:
            await message.reply_text(
                "Trimite-mi un mesaj non-gol ca să te pot ajuta."
            )
            return

        # Lazily build the registry + supervisor on first message so
        # import stays cheap and the bot can be instantiated even
        # when the memory stack is down.
        registry = context.application.bot_data.get("registry")
        if registry is None:
            from config.registry import ServiceRegistry
            registry = ServiceRegistry()
            context.application.bot_data["registry"] = registry

        from supervisor.supervisor import GoatSupervisor
        supervisor = GoatSupervisor(registry)

        warn_s = _CFG["turn_warn_seconds"]
        t0 = asyncio.get_event_loop().time()
        result = await supervisor.run(intent)
        elapsed = asyncio.get_event_loop().time() - t0
        if elapsed > warn_s:
            log.warning(
                "telegram: turn took %.1fs (warn threshold %ds)",
                elapsed, warn_s,
            )

        text = _truncate(result.summary or "")
        for chunk in _split_message(text):
            await message.reply_text(chunk)
    except Exception as exc:  # noqa: BLE001 — never break the chat
        log.exception("handle_update failed: %s", exc)
        try:
            if update.effective_message is not None:
                await update.effective_message.reply_text(
                    "A apărut o eroare. Te rog încearcă din nou."
                )
        except Exception:  # noqa: BLE001
            pass


def _on_post_shutdown(application) -> None:
    """Best-effort cleanup of resources owned by the bot.

    Logs the shutdown; nothing critical to close (the registry's
    Redis / Letta connections are managed by their own pools).
    """
    log.info("telegram_bot: post_shutdown — bye")


def _build_application(token: str):
    """Construct the PTB ``Application`` with handlers wired."""
    from telegram.ext import Application, MessageHandler, filters
    app = Application.builder().token(token).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update)
    )
    app.post_shutdown = _on_post_shutdown  # type: ignore[assignment]
    return app


def run_polling(token: str | None = None) -> None:
    """Blocking entry point — runs the bot until SIGINT.

    Args:
        token: Telegram bot token. Falls back to ``TELEGRAM_BOT_TOKEN``
            env var, then to ``config/goat.toml [channels].telegram_token``.

    Note:
        Configures ``logging.basicConfig`` so the bot is runnable
        as a standalone entry point without external setup.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    tok = (
        token
        or _env_token()
        or _config_token()
    )
    if not tok:
        raise RuntimeError(
            "telegram: no token (set TELEGRAM_BOT_TOKEN or "
            "config/goat.toml [channels].telegram_token)"
        )
    app = _build_application(tok)
    log.info("telegram_bot: starting polling")
    app.run_polling()


def _env_token() -> str | None:
    """Read TELEGRAM_BOT_TOKEN from env (lazy import os)."""
    import os
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def _config_token() -> str | None:
    """Read telegram_token from config/goat.toml [channels]."""
    try:
        from config.modular_loader import _load_raw  # type: ignore
        data = _load_raw("goat.toml") or {}
        ch = (data.get("channels", {}) or {}).get("telegram_token")
        if isinstance(ch, str) and ch.strip():
            return ch.strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("telegram: config token load failed: %s", exc)
    return None