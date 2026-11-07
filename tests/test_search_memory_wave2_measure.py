"""search_memory_wave2: separate, measurement-only count of wave-2 neighbours in search_memory calls."""
from __future__ import annotations

import asyncio
import logging

import tools.memory_tools as mt


def _r(mid, score, **kw):
    return {"content": f"text {mid}", "blended_score": score,
            "metadata": {"message_id": mid, "timestamp": 1_700_000_000, "sequence_number": 1}, **kw}


def _merged():
    """The post-#27 shape: 20 originals, then the neighbours of the surviving seeds at the tail."""
    orig = [_r(f"m{i}", 1 - i / 100) for i in range(20)]
    return orig + [_r("n1", 0.99, mechanisms=["wave2"], wave2_of="m0"), _r("n2", 0.98, mechanisms=["wave2"], wave2_of="m1")]


def _run(monkeypatch, merged):
    async def fake_retrieve(layers, chat_id, query, state, activation):
        return merged, False, None, {}
    monkeypatch.setattr(mt, "retrieve", fake_retrieve)
    return asyncio.run(mt.build_search_memory_tool(object()).handler(query="q", chat_id="c1"))


def test_output_to_the_model_is_unchanged(monkeypatch):
    merged = _merged()
    out = _run(monkeypatch, merged)
    assert out == mt._fmt_results(merged[:mt._LIMIT])  # exactly what the tool returned before this change


def test_logs_neighbours_found_vs_shown(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tools.memory_tools")
    _run(monkeypatch, _merged())
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("search_memory_wave2"))
    assert line == ("search_memory_wave2 chat=c1 shown=5 wave2_found=2 wave2_shown=0 wave2_cut_by_limit=2 "
                    "wave2_shown_ranks=[]")


def test_counts_neighbours_that_do_get_shown(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tools.memory_tools")
    merged = [_r("m0", 0.9), _r("n1", 0.89, mechanisms=["wave2"], wave2_of="m0")]
    _run(monkeypatch, merged)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("search_memory_wave2"))
    assert "shown=2 wave2_found=1 wave2_shown=1 wave2_cut_by_limit=0 wave2_shown_ranks=[2]" in line


def test_measurement_never_breaks_the_tool(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("log down")
    monkeypatch.setattr(mt.log, "info", boom)  # a failure inside the measurement must be swallowed
    merged = _merged()
    assert _run(monkeypatch, merged) == mt._fmt_results(merged[:mt._LIMIT])
