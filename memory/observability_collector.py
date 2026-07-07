"""
memory.observability_collector — per-request builder of a MemoryObservation.

The orchestrator owns one ``ObservationCollector`` per turn. It records stage
start/end times and sets the scalar fields; the block-derived fields (tokens
per tier, results used, source tier) and the coarse intent category are
derived here so the orchestrator stays lean. Stage timing uses ``start_stage``
/``end_stage`` with the stage name (``classify``/``search``/``assemble``/
``inject``); ``end_stage`` writes ``latency_<stage>`` on the observation.
"""
from __future__ import annotations

import time

from memory.budget import estimate_tokens
from memory.observability import MemoryObservation

# Block headers produced by MemoryLayers.assemble_context — used to attribute
# tokens to a tier without changing the per-block bookkeeping.
_IDENTITY_HEADER = "[Identity]"
_HISTORY_HEADER = "[Conversation History]"
_RELATED_HEADER = "[Context recuperat din istoric]"

# Confidence-tier thresholds for the coarse intent category (no real
# classifier exists yet; AITS yields confidence, not a category). Hardcoded
# here — no config knob — because the prefetch confidence gate is gone and
# these only label the analytics tier, never gate any behaviour.
_GREETING_CONFIDENCE = 0.3
_RECALL_CONFIDENCE = 0.4


class ObservationCollector:
    """Per-request collector that builds and logs one ``MemoryObservation``."""

    def __init__(self, chat_id: str, user_message: str) -> None:
        """Start a new observation for ``chat_id`` (message truncated)."""
        self.obs = MemoryObservation(
            timestamp=time.time(),
            chat_id=chat_id,
            user_message=user_message[:200],
            confidence=0.0,
            complexity=0.0,
            intent_category="conversational",
            budget_allocated=0,
            budget_used=0,
            cache_hit=False,
            cache_miss=False,
        )
        self._start_times: dict[str, float] = {}

    def start_stage(self, stage: str) -> None:
        """Record the start time of ``stage``."""
        self._start_times[stage] = time.time()

    def end_stage(self, stage: str) -> float:
        """Record end time, write ``latency_<stage>``, return the latency."""
        start = self._start_times.get(stage)
        if start is None:
            return 0.0
        latency = time.time() - start
        setattr(self.obs, f"latency_{stage}", latency)
        return latency

    def set_confidence(self, confidence: float) -> None:
        """Set the AITS confidence."""
        self.obs.confidence = confidence

    def set_complexity(self, complexity: float) -> None:
        """Set the AITS complexity."""
        self.obs.complexity = complexity

    def set_intent(self, intent: str) -> None:
        """Set the derived intent category."""
        self.obs.intent_category = intent

    def set_budget(self, allocated: int, used: int) -> None:
        """Set the AITS budget allocated and the tokens actually used."""
        self.obs.budget_allocated = allocated
        self.obs.budget_used = used

    def set_cache(self, hit: bool, key: str | None = None) -> None:
        """Set the L2.5 cache outcome (hit/miss) and report the cache key.

        The key was previously treated as internal and never stored; it is now
        propagated so the observability record shows what was actually looked up.
        """
        self.obs.cache_hit = hit
        self.obs.cache_miss = not hit
        self.obs.cache_key = key

    def set_prefetch(
        self, attempted: bool, succeeded: bool, timeout: bool,
        results_returned: int, blocks_used: int,
    ) -> None:
        """Set the L3 prefetch outcome."""
        self.obs.prefetch_attempted = attempted
        self.obs.prefetch_succeeded = succeeded
        self.obs.prefetch_timeout = timeout
        self.obs.prefetch_results_returned = results_returned
        self.obs.prefetch_blocks_used = blocks_used

    def set_prefetch_blocks_used(self, blocks_used: int) -> None:
        """Patch in the real L3 usage count once assemble_context has run."""
        self.obs.prefetch_blocks_used = blocks_used

    def set_prefetch_mechanisms(
        self, warm_served: bool,
        thematic: int, specific_key: int,
    ) -> None:
        """Set warm-served flag and per-mechanism result counts."""
        self.obs.warm_served = warm_served
        self.obs.prefetch_thematic_count = thematic
        self.obs.prefetch_specific_key_count = specific_key

    def set_llm_usage(self, prompt: int, completion: int, total: int, calls: int) -> None:
        """Set real API token counts from response.usage (billed, not estimated)."""
        self.obs.tokens_prompt_api = prompt
        self.obs.tokens_completion_api = completion
        self.obs.tokens_total_api = total
        self.obs.llm_calls = calls

    def set_llm_latency_breakdown(self, first: float, tool_rounds: float) -> None:
        """Set per-phase LLM latency: first planning call vs. tool-round calls."""
        self.obs.latency_llm_first = first
        self.obs.latency_tool_rounds = tool_rounds

    def set_activation(
        self, state: str, thread_break: bool = False,
        write_kind: str = "", enriching_refresh: bool = False,
    ) -> None:
        """Set the L2.5 activation outcome for the turn.

        ``state`` is ``cold`` / ``warm`` / ``drift`` (how the turn related to the
        per-chat thread activation); ``thread_break`` is True when a consensus
        shift ended an existing thread; ``write_kind`` is ``enriching`` /
        ``filing`` / ``none``; ``enriching_refresh`` is True when an on-thread
        write refreshed the activation in place this turn.
        """
        self.obs.activation_state = state
        self.obs.thread_break = thread_break
        self.obs.write_kind = write_kind
        self.obs.enriching_refresh = enriching_refresh

    def categorize_intent(self, confidence: float) -> str:
        """Derive a coarse intent category from the confidence tier.

        ``>= _RECALL_CONFIDENCE`` → recall; ``< _GREETING_CONFIDENCE`` →
        greeting; else conversational. A real intent classifier is future
        work; these labels are analytics-only and never gate prefetch.
        """
        if confidence >= _RECALL_CONFIDENCE:
            return "recall"
        if confidence < _GREETING_CONFIDENCE:
            return "greeting"
        return "conversational"

    def set_context_from_blocks(
        self, blocks: list[str], results_found: int = 0, results_used: int = 0,
    ) -> None:
        """Derive tokens/source from assembled prompt blocks and set result counts.

        Attributes each block to a tier by its header (``startswith``, no regex)
        and sums estimated tokens. ``results_found`` is the search result count
        (from the orchestrator); ``results_used`` is how many actually fit the
        budget (returned by ``assemble_context``). Sets tokens_*, results_found,
        results_used, source_tier (highest contributing tier), and
        budget_used (== tokens_injected).
        """
        l0_l1 = l2 = l3 = 0
        tier = "none"
        for block in blocks:
            if block.startswith(_IDENTITY_HEADER):
                l0_l1 = estimate_tokens(block)
                if tier == "none":
                    tier = "permanent"
            elif block.startswith(_HISTORY_HEADER):
                l2 = estimate_tokens(block)
                tier = "working" if tier != "episodic" else tier
            elif block.startswith(_RELATED_HEADER):
                l3 = estimate_tokens(block)
                tier = "episodic"
        injected = l0_l1 + l2 + l3
        self.obs.results_found = results_found
        self.obs.tokens_l0_l1 = l0_l1
        self.obs.tokens_l2 = l2
        self.obs.tokens_l3 = l3
        self.obs.tokens_injected = injected
        self.obs.results_used = results_used
        self.obs.source_tier = tier
        self.obs.budget_used = injected

    def finish(self, total_latency: float) -> None:
        """Set the total latency and emit the structured observation."""
        self.obs.latency_total = total_latency
        self.obs.log()