"""admin_panel.voice_live.providers — which provider adapter a name stands for.

``fake`` is the scripted provider (tests and dev); ``gemini`` is Gemini Live and needs ``GEMINI_API_KEY``; ``openai``
is OpenAI Realtime and needs ``OPENAI_API_KEY``. A factory
returns None when its provider cannot be used (no key), and an unknown name means "not configured", never a guess.
"""
from __future__ import annotations

from typing import Callable

from admin_panel.voice_live.fake import FakeProvider
from admin_panel.voice_live.gemini import make_gemini
from admin_panel.voice_live.openai import make_openai
from admin_panel.voice_live.provider import Provider

__all__ = ["PROVIDERS", "make_provider", "is_available"]

PROVIDERS: dict[str, Callable[[], Provider | None]] = {"fake": FakeProvider, "gemini": make_gemini, "openai": make_openai}


def make_provider(name: str | None) -> Provider | None:
    factory = PROVIDERS.get((name or "").strip().lower())
    return factory() if factory is not None else None


def is_available(name: str | None) -> bool:
    """Whether the named provider can be started on this server (the page only offers the live voice when it can)."""
    return make_provider(name) is not None
