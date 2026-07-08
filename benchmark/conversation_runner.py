"""benchmark.conversation_runner — full-cycle warm/cold conversation benchmark (spec §4.4).

Exercises the passive warm-serving path end-to-end: preload -> orchestrator.run()
(fires the post-turn prefetch daemon) -> drain_background() -> orchestrator.run()
again (now warm-served) -- vs. a cold baseline with no prior turn and no drain.
Reuses BenchmarkRunner's snapshot_analytics/diff_analytics (benchmark.runner)
so context_blocks/warm_served are captured the same way run_single captures
them, and scores each turn's response with Evaluator.groundedness_judge.
"""
from __future__ import annotations

import time
import uuid

from benchmark.evaluator import Evaluator
from benchmark.runner import diff_analytics, snapshot_analytics

__all__ = ["run_conversation"]


async def run_conversation(orchestrator, registry, case: dict) -> dict:
    """Run a mined case's warm and cold paths; return both turns' captured data."""
    warm = await _run_turn_warm(orchestrator, registry, case)
    cold = await _run_turn_cold(orchestrator, registry, case)
    return {"warm": warm, "cold": cold}


async def _run_one_turn(orchestrator, registry, chat_id: str, query: str) -> dict:
    """Run a single orchestrator turn; capture response, latency, context_blocks,
    warm_served (via diff_analytics), and the groundedness verdict."""
    analytics = registry.memory_analytics
    before = snapshot_analytics(analytics)
    captured: list[list[str]] = []
    t0 = time.time()
    response = await orchestrator.run(query, chat_id, on_context_assembled=captured.append)
    latency = time.time() - t0
    blocks = captured[-1] if captured else []
    diff = diff_analytics(before, snapshot_analytics(analytics), latency, response, None, blocks)
    verdict = await Evaluator.groundedness_judge(response, "\n\n".join(blocks), registry.llm_client)
    return {"response": response, "chat_id": chat_id, "groundedness": verdict, **diff}


async def _run_turn_warm(orchestrator, registry, case: dict) -> dict:
    """Preload lead-in content, run turn 1, drain the prefetch daemon, run turn 2 (warm)."""
    layers = registry.memory_layers
    chat_id = f"bench-warm-{uuid.uuid4().hex[:12]}"
    lead_in = case.get("lead_in_turns") or [case["expected_fact"]]
    for content in lead_in:
        await layers.store_episodic(chat_id, content)
    await orchestrator.run(lead_in[-1], chat_id)
    await orchestrator.drain_background()
    return await _run_one_turn(orchestrator, registry, chat_id, case["query"])


async def _run_turn_cold(orchestrator, registry, case: dict) -> dict:
    """Same query, brand-new chat_id, no lead-in, no drain — a single cold turn."""
    chat_id = f"bench-cold-{uuid.uuid4().hex[:12]}"
    return await _run_one_turn(orchestrator, registry, chat_id, case["query"])
