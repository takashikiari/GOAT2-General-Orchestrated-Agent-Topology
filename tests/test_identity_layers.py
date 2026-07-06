"""Tests for MemoryLayers identity prompt plumbing (no real backends)."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from memory.config import IDENTITY_BASE_PROMPT


class _FakePermanent:
    def __init__(self, override=None, raise_on_set=False):
        self._override = override
        self._raise_on_set = raise_on_set
        self.set_calls: list[str] = []

    async def get_identity_override(self):
        return self._override

    async def set_identity_override(self, text):
        if self._raise_on_set:
            raise Exception("Letta down")
        self.set_calls.append(text)

    async def get_all_facts(self):
        return {}


def _make_layers(override=None, raise_on_set=False):
    from memory.layers import MemoryLayers
    layers = MemoryLayers.__new__(MemoryLayers)
    layers._permanent = _FakePermanent(override=override, raise_on_set=raise_on_set)
    layers._working = AsyncMock()
    layers._working.get_messages.return_value = []
    layers._episodic = AsyncMock()
    layers._cache = AsyncMock()
    layers._activation = AsyncMock()
    return layers


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_override_when_set():
    layers = _make_layers(override="You are a pirate assistant.")
    result = await layers.get_identity_prompt()
    assert result == "You are a pirate assistant."


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_base_when_no_override():
    layers = _make_layers(override=None)
    result = await layers.get_identity_prompt()
    assert result == IDENTITY_BASE_PROMPT


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_base_on_exception():
    layers = _make_layers()
    layers._permanent.get_identity_override = AsyncMock(side_effect=Exception("fail"))
    result = await layers.get_identity_prompt()
    assert result == IDENTITY_BASE_PROMPT


@pytest.mark.asyncio
async def test_set_identity_override_delegates_to_permanent():
    layers = _make_layers()
    await layers.set_identity_override("New persona.")
    assert layers._permanent.set_calls == ["New persona."]


@pytest.mark.asyncio
async def test_assemble_context_uses_provided_identity_prompt():
    layers = _make_layers()
    layers._working.get_messages.return_value = []
    blocks, _ = await layers.assemble_context(
        "chat1", identity_prompt="Custom prompt here."
    )
    assert any("Custom prompt here." in b for b in blocks)


@pytest.mark.asyncio
async def test_assemble_context_falls_back_to_base_when_none():
    layers = _make_layers()
    layers._working.get_messages.return_value = []
    blocks, _ = await layers.assemble_context("chat1", identity_prompt=None)
    assert any(IDENTITY_BASE_PROMPT in b for b in blocks)
