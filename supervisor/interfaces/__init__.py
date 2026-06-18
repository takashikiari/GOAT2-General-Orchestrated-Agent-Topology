"""supervisor.interfaces — adapters that wire the supervisor to
external channels.

Currently:
  - ``telegram_bot`` — Telegram adapter (no regex, no DSML
    processing here; that's done upstream in
    ``pipeline.goat_call``).

The interface package is intentionally tiny: each channel
adapter should be a few hundred lines, no more. Complex
channel-specific logic (commands, media, payments, etc.)
belongs in a dedicated external service, not here.
"""
from __future__ import annotations

from supervisor.interfaces import telegram_bot

__all__ = ["telegram_bot"]
