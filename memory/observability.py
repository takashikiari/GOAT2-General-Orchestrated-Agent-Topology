"""
memory.observability — structured per-request observation of memory behaviour.

A ``MemoryObservation`` is one JSON-serialisable record of a single orchestrator
turn: the AITS confidence/complexity/budget, the L2.5 cache outcome, latency per
stage, tokens injected per tier, the source tier that contributed, and the L3
prefetch outcome. One is emitted at INFO per request and fed to
``memory.analytics`` for aggregation. Stage names match the real pipeline —
``classify`` / ``search`` / ``assemble`` / ``inject`` (there is no rerank step;
the spec's ``latency_rerank`` is renamed ``latency_assemble``).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional

from utils.logging.setup import get_logger

log = get_logger(__name__)

# Privacy: the user message is truncated before being recorded.
_MAX_MESSAGE_CHARS = 200


@dataclass
class MemoryObservation:
    """Structured record of a single memory operation (one orchestrator turn).

    Populated by ``ObservationCollector`` and emitted via ``log()`` as a JSON
    line; aggregated by ``MemoryAnalytics``. Optional/cache fields default to
    ``0``/``False``/``""`` so a partial observation (e.g. raised mid-turn) still
    serialises cleanly.
    """

    timestamp: float
    chat_id: str
    user_message: str  # truncated to _MAX_MESSAGE_CHARS for privacy
    confidence: float
    complexity: float
    intent_category: str  # "recall" | "greeting" | "conversational"
    budget_allocated: int
    budget_used: int

    # L2.5 cache
    cache_hit: bool
    cache_miss: bool
    cache_key: Optional[str] = None

    # Latency (seconds) per stage. inject = prompt assembly only; llm holds the
    # (dominant) LLM API call(s); save = L2 working-memory persist.
    latency_classify: float = 0.0
    latency_search: float = 0.0
    latency_assemble: float = 0.0
    latency_inject: float = 0.0
    latency_llm: float = 0.0
    latency_save: float = 0.0
    latency_total: float = 0.0

    # Results
    results_found: int = 0
    results_used: int = 0
    source_tier: str = ""  # "episodic" | "working" | "permanent" | "none"

    # Tokens injected, per tier
    tokens_injected: int = 0
    tokens_l0_l1: int = 0
    tokens_l2: int = 0
    tokens_l3: int = 0

    # Real API token usage (from response.usage — billed counts, not estimated)
    tokens_prompt_api: int = 0
    tokens_completion_api: int = 0
    tokens_total_api: int = 0
    llm_calls: int = 0  # total LLM API calls this turn (first + tool rounds)

    # LLM latency breakdown: first planning call vs. subsequent tool-round calls
    latency_llm_first: float = 0.0
    latency_tool_rounds: float = 0.0

    # L3 prefetch
    prefetch_attempted: bool = False
    prefetch_succeeded: bool = False
    prefetch_timeout: bool = False
    prefetch_results_returned: int = 0  # raw L3 result count from prefetch daemon
    prefetch_blocks_used: int = 0       # how many actually fit the budget
    warm_served: bool = False           # True when results came from activation (no search ran)
    prefetch_thematic_count: int = 0    # results from thematic (cached) mechanism
    prefetch_temporal_count: int = 0    # results from temporal mechanism (0 if not run)
    prefetch_specific_key_count: int = 0  # results from specific_key mechanism (0 if not run)

    # L2.5 activation (brain thread state) — how the turn related to the
    # per-chat activation: cold (full search) / warm (served from activation) /
    # drift (targeted refresh), whether the thread broke (consensus shift), and
    # whether a write this turn enriched the activation in place.
    activation_state: str = ""
    thread_break: bool = False
    write_kind: str = ""
    enriching_refresh: bool = False

    def to_dict(self) -> dict:
        """Serialize this observation to a plain dict (for JSON logging)."""
        return asdict(self)

    def log(self) -> None:
        """Emit this observation as a structured JSON line at INFO."""
        log.info(json.dumps(self.to_dict(), default=str))