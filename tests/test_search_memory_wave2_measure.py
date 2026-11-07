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


def test_originals_unchanged_and_neighbours_follow_their_seed(monkeypatch):
    merged = _merged()
    out = _run(monkeypatch, merged)
    old = merged[:mt._LIMIT]  # what the tool showed before: the same 5 originals
    expected = [old[0], merged[20], old[1], merged[21]] + old[2:]  # n1 after m0, n2 after m1
    assert out == mt._fmt_results(expected)


def test_logs_neighbours_found_vs_shown(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tools.memory_tools")
    _run(monkeypatch, _merged())
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("search_memory_wave2"))
    assert line == ("search_memory_wave2 chat=c1 shown=7 wave2_found=2 wave2_shown=2 wave2_cut_by_limit=0 "
                    "wave2_shown_ranks=[2, 4]")


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
    assert "text n1" in _run(monkeypatch, merged)


def test_neighbour_of_a_seed_not_shown_is_dropped_and_parents_are_deduped():
    orig = [_r(f"m{i}", 1 - i / 100) for i in range(8)]
    n_hidden = _r("n7", 0.9, mechanisms=["wave2"], wave2_of="m7")      # m7 is the 8th: not shown
    n_dup = _r("m2", 0.95, mechanisms=["wave2"], wave2_of="m0")         # parent m2 already shown
    n_twice = [_r("n0", 0.99, mechanisms=["wave2"], wave2_of="m0")] * 2  # same neighbour twice
    out = mt._with_seed_neighbours(orig + [n_hidden, n_dup] + n_twice, 5)
    ids = [r["metadata"]["message_id"] for r in out]
    assert ids == ["m0", "n0", "m1", "m2", "m3", "m4"]
    assert [r for r in out if "wave2" not in r.get("mechanisms", [])] == orig[:5]


def test_no_neighbours_means_exactly_the_old_cut():
    orig = [_r(f"m{i}", 1 - i / 100) for i in range(9)]
    assert mt._with_seed_neighbours(orig, 5) == orig[:5]


def test_logs_every_shown_result_compactly(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tools.memory_tools")
    merged = _merged()
    out = _run(monkeypatch, merged)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("search_memory_shown"))
    assert "chat=c1 query='q' n=7 results=[(1, " in line
    assert line.count("'w2')") == 2 and "(2, " in line and "(7, " in line  # all 7 shown entries, neighbours tagged
    assert "text m0" not in line  # ids, times and sizes only, never the text
    assert out == mt._fmt_results(mt._with_seed_neighbours(merged, mt._LIMIT))  # output to the model unchanged


def test_shown_log_failure_never_breaks_the_tool(monkeypatch):
    monkeypatch.setattr(mt, "datetime", None)  # anything inside the measurement may fail
    merged = _merged()
    monkeypatch.setattr(mt, "_fmt_results", lambda rs: "ok")
    assert _run(monkeypatch, merged) == "ok"
