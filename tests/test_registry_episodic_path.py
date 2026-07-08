"""tests.test_registry_episodic_path — ServiceRegistry threads an episodic
storage-path override to EpisodicMemory (benchmark isolation, spec §10.1).

No I/O: EpisodicMemory's ChromaDB connection is lazy, only opened by
_get_collection() on first use, which this test never triggers.
"""
from __future__ import annotations

from registry.registry import ServiceRegistry


def test_default_registry_uses_configured_live_path():
    from memory.config import EPISODIC_STORAGE_PATH
    reg = ServiceRegistry()
    assert reg.episodic_memory._storage_path == EPISODIC_STORAGE_PATH


def test_registry_threads_custom_episodic_storage_path():
    reg = ServiceRegistry(episodic_storage_path="/tmp/chroma_data_benchmark")
    assert reg.episodic_memory._storage_path == "/tmp/chroma_data_benchmark"
