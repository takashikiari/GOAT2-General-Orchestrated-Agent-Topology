"""Noise fixes: chunk store telemetry off, ONNX and Chroma component DEBUG lines quiet, offline scripts never log into goat2.log."""
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


def test_chroma_component_start_debug_lines_are_quiet():
    from utils.logging import setup
    assert "chromadb.config" in setup._QUIET_LIBS


def test_offline_scripts_redirect_logs_away_from_live_file(monkeypatch):
    for mod in ("scripts.build_chunk_store", "scripts.compare_chunks"):
        monkeypatch.delenv("GOAT_LOG_DIR", raising=False)
        sys.modules.pop(mod, None)
        importlib.import_module(mod)
        assert os.environ["GOAT_LOG_DIR"].endswith("goat2-offline-logs")
        assert "/tmp/goat2/logs" not in os.environ["GOAT_LOG_DIR"]
        assert ".local/state/goat2" not in os.environ["GOAT_LOG_DIR"]   # nor into the live default


def test_compare_chunks_still_reads_the_live_log_not_its_own(monkeypatch, tmp_path):
    monkeypatch.delenv("GOAT_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sys.modules.pop("scripts.compare_chunks", None)
    mod = importlib.import_module("scripts.compare_chunks")
    assert mod._LIVE_LOG_DIR == tmp_path / "goat2" / "logs"


def test_default_log_dir_is_on_persistent_disk(monkeypatch, tmp_path):
    from pathlib import Path
    from utils.logging import setup
    assert setup.default_log_dir({}) == Path.home() / ".local" / "state" / "goat2" / "logs"
    assert setup.default_log_dir({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "goat2" / "logs"
    assert setup.default_log_dir({"XDG_STATE_HOME": "  "}) == Path.home() / ".local" / "state" / "goat2" / "logs"
    assert not str(setup.default_log_dir({})).startswith("/tmp")


def test_goat_log_dir_still_overrides(monkeypatch):
    # conftest.py sets GOAT_LOG_DIR for the whole test run: the override must win over the default
    from utils.logging import setup
    assert str(setup.LOG_FILE.parent) == os.environ["GOAT_LOG_DIR"]
