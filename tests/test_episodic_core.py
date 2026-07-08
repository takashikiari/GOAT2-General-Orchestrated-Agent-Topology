"""tests.test_episodic_core — EpisodicMemory storage-path injection.

Covers the benchmark-isolation gap (spec §10.1): EpisodicMemory must accept an
optional storage_path override so a benchmark run can point at
chroma_data_benchmark/ without touching the live collection, while still
defaulting to the configured live path when no override is given.
"""
from __future__ import annotations

import chromadb

from memory.config import EPISODIC_STORAGE_PATH
from memory.episodic import EpisodicMemory


class _FakeClient:
    """Captures the path PersistentClient was constructed with; no real ChromaDB I/O."""

    def __init__(self, path, settings=None):
        self.path = path

    def get_or_create_collection(self, name):
        return object()


def _patch_client(monkeypatch, captured: dict) -> None:
    def _fake(path, settings=None):
        captured["path"] = path
        return _FakeClient(path)
    monkeypatch.setattr(chromadb, "PersistentClient", _fake)


def test_default_storage_path_is_config_constant(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured)
    e = EpisodicMemory()
    e._get_collection()
    assert captured["path"] == EPISODIC_STORAGE_PATH


def test_custom_storage_path_overrides_default(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured)
    e = EpisodicMemory(storage_path="/tmp/chroma_data_benchmark")
    e._get_collection()
    assert captured["path"] == "/tmp/chroma_data_benchmark"
