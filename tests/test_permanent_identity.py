"""Tests for PermanentMemory identity stored inside the 'facts' block."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from memory.permanent.permanent import PermanentMemory


def _make_pm(agent_id="agent-1") -> tuple[PermanentMemory, MagicMock]:
    pm = PermanentMemory()
    pm._agent_id = agent_id
    http = AsyncMock()
    pm._http = http
    return pm, http


def _facts_resp(facts: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"value": json.dumps(facts)}
    r.raise_for_status = MagicMock()
    return r


def _ok_resp() -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {}
    r.raise_for_status = MagicMock()
    return r


@pytest.mark.asyncio
async def test_get_identity_override_returns_value():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({"__identity__": "You are a pirate."})
    assert await pm.get_identity_override() == "You are a pirate."


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_when_missing():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({"some_fact": "value"})
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_empty():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({"__identity__": "   "})
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_exception():
    pm, http = _make_pm()
    http.get.side_effect = Exception("network error")
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_set_identity_override_stores_in_facts():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({})
    http.patch.return_value = _ok_resp()
    await pm.set_identity_override("You are Max.")
    http.patch.assert_called_once()
    saved = json.loads(http.patch.call_args[1]["json"]["value"])
    assert saved.get("__identity__") == "You are Max."


@pytest.mark.asyncio
async def test_set_identity_override_clears_when_empty():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({"__identity__": "Old identity."})
    http.patch.return_value = _ok_resp()
    await pm.set_identity_override("")
    http.patch.assert_called_once()
    saved = json.loads(http.patch.call_args[1]["json"]["value"])
    assert "__identity__" not in saved


@pytest.mark.asyncio
async def test_get_all_facts_excludes_identity_key():
    pm, http = _make_pm()
    http.get.return_value = _facts_resp({
        "user_name": "Gabriel",
        "__identity__": "You are a pirate.",
    })
    facts = await pm.get_all_facts()
    assert facts == {"user_name": "Gabriel"}
