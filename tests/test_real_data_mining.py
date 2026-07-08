"""tests.test_real_data_mining — LLM ground-truth generation + caching (spec §4.2)."""
from __future__ import annotations

import asyncio
import json

from benchmark.real_data_mining import generate_case, load_or_mine, mine_cases
from tests._orch_fakes import _Completions, _LLMClient


def _entry(content: str, message_id="msg1", chat_id="chat1") -> dict:
    return {
        "id": "row1", "content": content,
        "metadata": {"message_id": message_id, "chat_id": chat_id, "importance": 0.9},
    }


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
