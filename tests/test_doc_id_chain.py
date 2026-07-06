"""tests.test_doc_id_chain — store() returns doc_id, accepts pre-generated doc_id."""
from __future__ import annotations
import asyncio
import uuid
from unittest.mock import MagicMock, patch, AsyncMock


def test_store_returns_string():
    """EpisodicMemory.store() must return a str doc_id."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    ep._collection = mock_col
    result = asyncio.run(ep.store("chat1", "content", {"timestamp": 0.0}))
    assert isinstance(result, str)
    assert len(result) == 36  # UUID format


def test_store_uses_provided_doc_id():
    """EpisodicMemory.store() uses pre-generated doc_id when provided."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.count.return_value = 0
    ep._collection = mock_col
    pre_id = str(uuid.uuid4())
    result = asyncio.run(ep.store("chat1", "content", {"timestamp": 0.0}, doc_id=pre_id))
    assert result == pre_id
    # Verify col.add was called with our pre_id
    call_kwargs = mock_col.add.call_args
    assert call_kwargs[1]["ids"] == [pre_id] or call_kwargs[0][0] == [pre_id] or pre_id in str(mock_col.add.call_args)


def test_store_episodic_returns_string():
    """MemoryLayers.store_episodic() must return a str doc_id."""
    import time
    from memory.layers import MemoryLayers
    mock_working = MagicMock()
    mock_episodic = MagicMock()
    mock_episodic.store = AsyncMock(return_value="returned-id")
    mock_permanent = MagicMock()
    layers = MemoryLayers(mock_working, mock_episodic, mock_permanent)
    result = asyncio.run(layers.store_episodic("chat1", "content"))
    assert result == "returned-id"
