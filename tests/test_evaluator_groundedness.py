"""tests.test_evaluator_groundedness — Evaluator.groundedness_judge (spec §4.6)."""
from __future__ import annotations

import asyncio
import json

from benchmark.evaluator import Evaluator
from tests._orch_fakes import _Completions, _LLMClient


def test_groundedness_judge_parses_grounded_response():
    reply = json.dumps({
        "grounded": True, "hallucinated_claims": [], "answered_without_evidence": False,
    })
    llm = _LLMClient(_Completions(reply))
    verdict = asyncio.run(Evaluator.groundedness_judge("The meeting is at 9am.", "context: 9am meeting", llm))
    assert verdict == {"grounded": True, "hallucinated_claims": [], "answered_without_evidence": False}


def test_groundedness_judge_reports_hallucinated_claims():
    reply = json.dumps({
        "grounded": False,
        "hallucinated_claims": ["the meeting is in Paris"],
        "answered_without_evidence": False,
    })
    llm = _LLMClient(_Completions(reply))
    verdict = asyncio.run(Evaluator.groundedness_judge("The meeting is in Paris.", "context: 9am meeting", llm))
    assert verdict["grounded"] is False
    assert verdict["hallucinated_claims"] == ["the meeting is in Paris"]


def test_groundedness_judge_degrades_to_none_with_no_llm_client():
    verdict = asyncio.run(Evaluator.groundedness_judge("anything", "context", None))
    assert verdict == {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}


def test_groundedness_judge_degrades_to_none_on_malformed_json():
    llm = _LLMClient(_Completions("not json"))
    verdict = asyncio.run(Evaluator.groundedness_judge("anything", "context", llm))
    assert verdict["grounded"] is None


def test_groundedness_judge_wraps_string_hallucinated_claims():
    """Regression test: LLM returning hallucinated_claims as a string (not list).

    If the LLM returns "hallucinated_claims": "a single string" instead of
    ["a single string"], it must be wrapped in a list, not character-exploded.
    """
    reply = json.dumps({
        "grounded": False,
        "hallucinated_claims": "the meeting is in Paris",  # string, not list
        "answered_without_evidence": False,
    })
    llm = _LLMClient(_Completions(reply))
    verdict = asyncio.run(Evaluator.groundedness_judge("The meeting is in Paris.", "context: 9am meeting", llm))
    assert verdict["grounded"] is False
    # Must be wrapped as a single-element list, not character-exploded
    assert verdict["hallucinated_claims"] == ["the meeting is in Paris"]
