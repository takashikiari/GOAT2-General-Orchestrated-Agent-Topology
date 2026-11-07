"""Noise fixes: chunk store telemetry off, ONNX DEBUG lines quiet, offline scripts never log into goat2.log."""
import importlib
import os
import sys


def test_chunk_store_disables_chroma_telemetry(tmp_path):
    import posthog
    from memory.episodic.chunks import ChunkStore
    posthog.disabled = False
    ChunkStore(path=str(tmp_path / "c")).collection()
    assert posthog.disabled is True and posthog.capture("x", "y", "z") is None


def test_onnx_provider_debug_lines_are_quiet():
    from utils.logging import setup
    assert "chromadb.utils.embedding_functions" in setup._QUIET_LIBS


def test_offline_scripts_redirect_logs_away_from_live_file(monkeypatch):
    for mod in ("scripts.build_chunk_store", "scripts.compare_chunks"):
        monkeypatch.delenv("GOAT_LOG_DIR", raising=False)
        sys.modules.pop(mod, None)
        importlib.import_module(mod)
        assert os.environ["GOAT_LOG_DIR"].endswith("goat2-offline-logs")
        assert "/tmp/goat2/logs" not in os.environ["GOAT_LOG_DIR"]
