"""admin_panel.voice_live.providers — which provider adapter a name stands for.

Only the scripted ``fake`` exists so far (tests and dev: ``VOICE_LIVE_PROVIDER=fake``). The real adapters (``gemini``,
``openai``) are added in later phases; an unknown or not yet built name means "not configured", never a guess.
"""
from __future__ import annotations

from typing import Callable

from admin_panel.voice_live.fake import FakeProvider
from admin_panel.voice_live.provider import Provider

__all__ = ["PROVIDERS", "make_provider"]

PROVIDERS: dict[str, Callable[[], Provider]] = {"fake": FakeProvider}


def make_provider(name: str | None) -> Provider | None:
    factory = PROVIDERS.get((name or "").strip().lower())
    return factory() if factory is not None else None
