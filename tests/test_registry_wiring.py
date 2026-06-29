"""tests.test_registry_wiring — ServiceRegistry builds real types, never test fakes.

Guards against the class of bug where a test double leaks into the
production DI container: all three memory tier inits are lazy (no
network connections at construction time), so this test is safe to run
without Redis / ChromaDB / Letta available.
"""
from __future__ import annotations

from memory.layers import MemoryLayers
from registry.registry import ServiceRegistry


def test_service_registry_wires_real_memory_layers():
    """ServiceRegistry.memory_layers is a real MemoryLayers, not a test fake.

    When _archive_turn was introduced, _FakeLayers lacked store_episodic,
    causing a WARNING on every test run. The inverse risk — a fake somehow
    wired into a real ServiceRegistry — is caught here. All three tier inits
    are lazy (no I/O at construction), so this test needs no running backends.
    """
    reg = ServiceRegistry()
    assert isinstance(reg.memory_layers, MemoryLayers)
