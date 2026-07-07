"""tests.test_gliner_extractor — unit tests for GLiNERExtractor (no GLiNER installed)."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock
from memory.gliner_extractor import GLiNERExtractor, _infer_type


def test_infer_type_greeting_no_entities_short():
    assert _infer_type([], [], "hi") == "greeting"


def test_infer_type_fact_with_credential():
    assert _infer_type(["password"], ["credential"], "my password is abc") == "fact"


def test_infer_type_fact_with_entities():
    assert _infer_type(["Claude"], ["technology"], "I use Claude every day") == "fact"


def test_infer_type_conversation_no_entities_long():
    text = "I was thinking about things and how they work in general systems"
    assert _infer_type([], [], text) == "conversation"


def test_extract_returns_fallback_on_exception():
    """When GLiNER is not installed, extract() returns empty/conversation."""
    import asyncio
    extractor = GLiNERExtractor()
    result = asyncio.run(extractor.extract("hello world"))
    assert "entities" in result
    assert "entity_types" in result
    assert "memory_type" in result


def test_extract_with_mock_model():
    import asyncio
    import memory.gliner_extractor as mod
    extractor = GLiNERExtractor()
    mock_model = MagicMock()
    mock_model.predict_entities.return_value = [
        {"text": "GOAT", "label": "project"},
        {"text": "Gabriel", "label": "person"},
    ]
    with patch.object(mod, "_gliner_model", mock_model):
        result = asyncio.run(extractor.extract("Gabriel built GOAT"))
    assert "GOAT" in result["entities"]
    assert "project" in result["entity_types"]
    assert result["memory_type"] == "fact"
