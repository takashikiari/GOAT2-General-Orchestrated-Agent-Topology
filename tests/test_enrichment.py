"""tests.test_enrichment — unit tests for compute_importance and enrich_l3_entry."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_compute_importance_short():
    from memory.enrichment import compute_importance
    score = compute_importance("hi", "hello")
    assert 0.0 < score < 0.1  # very short


def test_compute_importance_long():
    from memory.enrichment import compute_importance
    user = " ".join(["word"] * 60)
    assistant = " ".join(["word"] * 60)
    score = compute_importance(user, assistant)
    assert score == 1.0  # 120 words → capped at 1.0


def test_compute_importance_medium():
    from memory.enrichment import compute_importance
    score = compute_importance("hello world today", "ok good bye")
    assert 0.0 < score < 1.0


def test_enrich_l3_entry_calls_update_metadata():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value={
        "entities": ["Claude"], "entity_types": ["technology"], "memory_type": "fact"
    })
    asyncio.run(enrich_l3_entry("doc-123", "user msg", "assistant msg", episodic, extractor))
    episodic.update_metadata.assert_called_once()
    call_args = episodic.update_metadata.call_args
    assert call_args[0][0] == "doc-123"
    updates = call_args[0][1]
    assert "importance" in updates
    assert "entities" in updates
    assert "memory_type" in updates


def test_enrich_l3_entry_no_extractor():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock()
    asyncio.run(enrich_l3_entry("doc-456", "msg", "reply", episodic, None))
    episodic.update_metadata.assert_called_once()
    updates = episodic.update_metadata.call_args[0][1]
    assert updates["memory_type"] == "conversation"


def test_enrich_l3_entry_handles_exception():
    from memory.enrichment import enrich_l3_entry
    episodic = MagicMock()
    episodic.update_metadata = AsyncMock(side_effect=Exception("db error"))
    # Should not raise
    asyncio.run(enrich_l3_entry("doc-789", "msg", "reply", episodic, None))
