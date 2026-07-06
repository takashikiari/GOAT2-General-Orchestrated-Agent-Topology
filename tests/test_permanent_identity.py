"""Tests for PermanentMemory identity block (mocked HTTP, no real Letta)."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from memory.permanent.permanent import PermanentMemory


def _make_pm(agent_id="agent-1") -> tuple[PermanentMemory, MagicMock]:
    """Return a PermanentMemory with pre-set agent_id and a mock HTTP client."""
    pm = PermanentMemory()
    pm._agent_id = agent_id
    http = AsyncMock()
    pm._http = http
    return pm, http


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


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
    await pm.set_identity_override("You are a helpful assistant named Max.")
    http.patch.assert_called_once()
    call_kwargs = http.patch.call_args
    assert "identity" in str(call_kwargs)
    assert "You are a helpful assistant named Max." in str(call_kwargs)


@pytest.mark.asyncio
async def test_set_identity_override_creates_block_on_404():
    # Two-step flow: POST /v1/blocks → PATCH /v1/agents/{id}/core-memory/blocks/{block_id}
    pm, http = _make_pm()
    # First PATCH (by label) returns 404; second PATCH (by block_id) returns 200.
    http.patch.side_effect = [_resp(404), _resp(200)]
    create_resp = _resp(200, {"id": "block-abc"})
    http.post.return_value = create_resp
    await pm.set_identity_override("New identity.")
    assert http.post.call_count == 1
    first_call = str(http.post.call_args_list[0])
    assert "/v1/blocks" in first_call
    assert "identity" in first_call
    assert http.patch.call_count == 2
    second_patch = str(http.patch.call_args_list[1])
    assert "block-abc" in second_patch
