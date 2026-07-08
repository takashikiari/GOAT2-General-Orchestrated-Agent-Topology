"""tests.test_real_data_mining — LLM ground-truth generation + caching (spec §4.2)."""
from __future__ import annotations

import asyncio
import json

from benchmark.real_data_mining import generate_case, load_or_mine, mine_cases
from tests._orch_fakes import _Completions, _LLMClient


def _entry(content: str, message_id="msg1", chat_id="chat1", id="row1") -> dict:
    return {
        "id": id, "content": content,
        "metadata": {"message_id": message_id, "chat_id": chat_id, "importance": 0.9},
    }


class _CountingCompletions(_Completions):
    """Wraps _Completions to count how many LLM calls were actually made."""

    def __init__(self, content="ok"):
        super().__init__(content=content)
        self.call_count = 0

    async def create(self, **kw):
        self.call_count += 1
        return await super().create(**kw)


def test_generate_case_parses_llm_json_response():
    content = " ".join(["word"] * 20)
    reply = json.dumps({"query": "What time is the appointment?", "expected_fact": "9am"})
    llm = _LLMClient(_Completions(reply))
    case = asyncio.run(generate_case(_entry(content), llm))
    assert case["query"] == "What time is the appointment?"
    assert case["expected_fact"] == "9am"
    assert case["message_id"] == "msg1"
    assert case["chat_id_source"] == "chat1"
    assert case["lead_in_turns"] == [content]


def test_generate_case_returns_none_on_malformed_json():
    llm = _LLMClient(_Completions("not json at all"))
    case = asyncio.run(generate_case(_entry("x" * 100), llm))
    assert case is None


def test_generate_case_returns_none_on_empty_fields():
    reply = json.dumps({"query": "", "expected_fact": ""})
    llm = _LLMClient(_Completions(reply))
    case = asyncio.run(generate_case(_entry("x" * 100), llm))
    assert case is None


def test_mine_cases_skips_short_entries_and_failed_generations():
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    entries = [_entry("hi"), _entry(long_content)]  # first too short to be a candidate
    llm = _LLMClient(_Completions(good_reply))
    cases = asyncio.run(mine_cases(entries, llm))
    assert len(cases) == 1
    assert cases[0]["query"] == "q"


def test_load_or_mine_caches_to_disk(tmp_path):
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    llm = _LLMClient(_Completions(good_reply))
    cache_path = tmp_path / "real_recall_cases.json"

    cases = asyncio.run(load_or_mine([_entry(long_content)], llm, cache_path))
    assert len(cases) == 1
    assert cache_path.exists()

    # Second call must not re-mine — swap in a client that would error if called.
    class _ExplodingCompletions:
        async def create(self, **kw):
            raise AssertionError("mining ran again; cache was not used")

    class _ExplodingChat:
        completions = _ExplodingCompletions()

    class _ExplodingClient:
        chat = _ExplodingChat()

    cached = asyncio.run(load_or_mine([_entry(long_content)], _ExplodingClient(), cache_path))
    assert cached == cases


def test_mine_cases_stops_early_once_limit_valid_cases_found():
    """First end-to-end run (2026-07-08): mining ran over ~all 1777 rows for a
    --limit 3 test, taking ~50 minutes. mine_cases must stop as soon as it has
    `limit` valid cases instead of exhausting every candidate."""
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    entries = [_entry(long_content, message_id=f"msg{i}", id=f"row{i}") for i in range(5)]
    completions = _CountingCompletions(good_reply)
    llm = _LLMClient(completions)

    cases = asyncio.run(mine_cases(entries, llm, limit=2))

    assert len(cases) == 2
    assert completions.call_count == 2  # stopped early — didn't mine all 5


def test_mine_cases_without_limit_mines_every_candidate():
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    entries = [_entry(long_content, message_id=f"msg{i}", id=f"row{i}") for i in range(3)]
    completions = _CountingCompletions(good_reply)
    llm = _LLMClient(completions)

    cases = asyncio.run(mine_cases(entries, llm))

    assert len(cases) == 3
    assert completions.call_count == 3


def test_load_or_mine_passes_limit_through_when_mining(tmp_path):
    long_content = " ".join(["word"] * 20)
    good_reply = json.dumps({"query": "q", "expected_fact": "f"})
    entries = [_entry(long_content, message_id=f"msg{i}", id=f"row{i}") for i in range(5)]
    completions = _CountingCompletions(good_reply)
    llm = _LLMClient(completions)
    cache_path = tmp_path / "cases.json"

    cases = asyncio.run(load_or_mine(entries, llm, cache_path, limit=2))

    assert len(cases) == 2
    assert completions.call_count == 2


def test_load_or_mine_slices_cached_results_to_limit(tmp_path):
    cache_path = tmp_path / "cases.json"
    cache_path.write_text(json.dumps([{"id": str(i)} for i in range(5)]))

    cases = asyncio.run(load_or_mine([], None, cache_path, limit=2))

    assert len(cases) == 2
