"""tests.test_chat_id_scoped_search — chat_id_filter parameter on search/search_episodic_with_cache."""
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock, AsyncMock


def _make_episodic_mock(return_docs=None):
    ep = MagicMock()
    ep._write_lock = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    ep.search = AsyncMock(return_value=return_docs or [])
    return ep


def test_search_episodic_passes_chat_id_filter():
    """layers.search_episodic passes chat_id_filter to episodic.search."""
    from memory.layers import MemoryLayers
    ep = _make_episodic_mock()
    layers = MemoryLayers(MagicMock(), ep, MagicMock())
    asyncio.run(layers.search_episodic("query", chat_id_filter="chat42"))
    call_kwargs = ep.search.call_args[1]
    assert call_kwargs.get("chat_id_filter") == "chat42"


def test_search_cache_key_differs_with_chat_id_filter():
    """Different chat_id_filter values produce different cache keys."""
    from memory.layers import MemoryLayers
    key_global = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter=None)
    key_scoped = MemoryLayers._search_cache_key("query", topic_id=None, chat_id_filter="chat42")
    assert key_global != key_scoped


def test_episodic_search_chat_id_filter_adds_clause():
    """EpisodicMemory.search adds chat_id clause when chat_id_filter given."""
    from memory.episodic.episodic import EpisodicMemory
    ep = EpisodicMemory()
    mock_col = MagicMock()
    mock_col.query.return_value = {
        "documents": [[]], "metadatas": [[]], "distances": [[]]
    }
    ep._collection = mock_col
    asyncio.run(ep.search("query", chat_id_filter="chat-xyz"))
    call_kwargs = mock_col.query.call_args[1]
    where = call_kwargs.get("where") or {}
    # chat_id should be in where clause (either direct or in $and)
    where_str = str(where)
    assert "chat-xyz" in where_str
