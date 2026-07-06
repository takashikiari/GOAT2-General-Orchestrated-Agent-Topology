"""Tests for PermanentMemory identity block (mocked HTTP, no real Letta)."""
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


def _resp(status: int, body=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {} if body is None else body
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


def _facts_resp(facts: dict) -> MagicMock:
    return _resp(200, {"value": json.dumps(facts)})


@pytest.mark.asyncio
async def test_get_identity_override_returns_value():
    pm, http = _make_pm()
    http.get.return_value = _resp(200, {"value": "You are a pirate."})
    assert await pm.get_identity_override() == "You are a pirate."


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_404():
    pm, http = _make_pm()
    http.get.return_value = _resp(404)
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_empty():
    pm, http = _make_pm()
    http.get.return_value = _resp(200, {"value": "   "})
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_exception():
    pm, http = _make_pm()
    http.get.side_effect = Exception("network error")
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_set_identity_override_patches_existing_block():
    pm, http = _make_pm()
    http.patch.return_value = _resp(200)
    await pm.set_identity_override("You are Max.")
    http.patch.assert_called_once()
    assert "identity" in str(http.patch.call_args)
    assert "You are Max." in str(http.patch.call_args)


@pytest.mark.asyncio
async def test_set_identity_override_recreates_agent_on_404():
    # Old agent without identity block: PATCH 404 → delete → recreate → PATCH identity
    pm, http = _make_pm()
    http.patch.side_effect = [_resp(404), _resp(200)]  # first=404, second=identity set
    http.get.side_effect = [
        _facts_resp({}),  # _get_facts() backup
        _resp(200, []),   # _resolve_agent_id() search after reset → empty list
    ]
    http.delete.return_value = _resp(200)
    http.post.return_value = _resp(200, {"id": "new-agent-id"})
    await pm.set_identity_override("New identity.")
    assert http.delete.call_count == 1
    assert http.post.call_count == 1  # agent recreation
    assert http.patch.call_count == 2  # 404 attempt + success
    assert pm._agent_id == "new-agent-id"
    final_patch = str(http.patch.call_args_list[1])
    assert "new-agent-id" in final_patch
    assert "New identity." in final_patch
