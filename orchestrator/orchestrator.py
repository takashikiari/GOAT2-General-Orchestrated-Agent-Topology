"""orchestrator.orchestrator — stateless LLM driver with AITS budgeting, async prefetch, single-round tool calling, and per-request observability."""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from config import settings
from memory.aits import (
    calculate_complexity_from_query,
    calculate_confidence_from_query,
    calculate_intent_budget,
)
from memory.config import (
    ANALYTICS_LOG_INTERVAL,
    MAX_RESULTS_PER_SEARCH,
    PREFETCH_CONFIDENCE_THRESHOLD,
    PREFETCH_TIMEOUT,
)
from memory.observability_collector import ObservationCollector
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from orchestrator.tools import ToolDefinition
    from registry.registry import ServiceRegistry

log = get_logger(__name__)

# Guidance appended to the system prompt when the search_memory tool is
# configured, so GOAT knows to fetch L3 on demand rather than claim it doesn't
# recall something. A constant (content, not a tunable), like _BASE_IDENTITY.
_SEARCH_MEMORY_GUIDANCE = (
    "If the user references something not visible in the conversation above, "
    "use search_memory before saying you don't recall it."
)

# Guidance appended when the store_memory tool is configured, so GOAT knows it
# can persist things worth remembering across sessions. A constant (content),
# like _SEARCH_MEMORY_GUIDANCE.
_STORE_MEMORY_GUIDANCE = (
    "You can store important information for future sessions using the "
    "store_memory tool. Use it when the user shares preferences, decisions, "
    "or facts worth remembering."
)


class Orchestrator:
    """Stateless (intent, chat_id)→LLM→reply driver with AITS + observability.

    All memory access flows through ``registry.memory_layers`` (the Backend
    Mapper); the orchestrator never imports the physical tiers. Per turn it
    computes a dynamic AITS token budget (confidence + complexity), runs a
    bounded-time L3 prefetch (skipped below a confidence threshold; the
    ``search_memory`` tool remains the fallback), then assembles context via
    ``memory_layers.assemble_context`` (L0+L1 protected, L2 protected to
    ``L2_CONTEXT_CAP``, L3 AITS-gated). One ``ObservationCollector`` records the
    turn (latency per stage, tokens per tier, cache/prefetch outcome) and feeds
    the registry-owned ``memory_analytics`` aggregator. Memory is the kernel's
    context only; tools and agents are separate systems, not budget inputs.
    """

    def __init__(self, registry: ServiceRegistry, tools: list[ToolDefinition] | None = None) -> None:
        self._registry = registry
        self._tools = tools or []

    def _has_search_memory(self) -> bool:
        """True when the ``search_memory`` tool is configured for this orchestrator."""
        return any(t.name == "search_memory" for t in self._tools)

    def _has_store_memory(self) -> bool:
        """True when the ``store_memory`` tool is configured for this orchestrator."""
        return any(t.name == "store_memory" for t in self._tools)

    async def run(self, intent: str, chat_id: str) -> str:
        """Single turn: AITS budget → bounded-time L3 prefetch → assemble → LLM → save.

        Each stage is timed (classify / search / assemble / inject) by an
        ``ObservationCollector``; on success the observation is recorded into
        ``memory_analytics`` and a report is logged every
        ``ANALYTICS_LOG_INTERVAL`` requests. On exception the partial
        observation is recorded before re-raising. The LLM call + tool round
        fall inside the ``inject`` stage (the biggest latency), and the second
        (tool-result) LLM call never receives tools, so runaway tool loops are
        structurally impossible.
        """
        layers = self._registry.memory_layers
        analytics = self._registry.memory_analytics
        collector = ObservationCollector(chat_id, intent)
        start = time.time()
        try:
            # 1. AITS dynamic budget (classify stage).
            collector.start_stage("classify")
            confidence = calculate_confidence_from_query(intent)
            complexity = calculate_complexity_from_query(intent)
            budget = calculate_intent_budget(confidence, complexity)
            collector.set_confidence(confidence)
            collector.set_complexity(complexity)
            collector.set_intent(collector.categorize_intent(confidence, PREFETCH_CONFIDENCE_THRESHOLD))
            collector.set_budget(budget, 0)
            collector.end_stage("classify")
            log.info(
                "AITS budget=%d confidence=%.2f complexity=%.2f chat=%s",
                budget, confidence, complexity, chat_id,
            )
            # 2. Bounded-time L3 prefetch (search stage, non-blocking).
            collector.start_stage("search")
            l3_results: list[dict] = []
            cache_hit = False
            prefetch_attempted = False
            prefetch_succeeded = False
            prefetch_timeout = False
            if confidence >= PREFETCH_CONFIDENCE_THRESHOLD:
                prefetch_attempted = True
                log.debug("prefetch started chat=%s", chat_id)
                try:
                    l3_results, cache_hit = await asyncio.wait_for(
                        layers.search_episodic_with_cache(chat_id, intent, limit=MAX_RESULTS_PER_SEARCH),
                        timeout=PREFETCH_TIMEOUT,
                    )
                    prefetch_succeeded = True
                    log.info("prefetch ok chat=%s hits=%d", chat_id, len(l3_results))
                except asyncio.TimeoutError:
                    prefetch_timeout = True
                    log.warning("prefetch timed out chat=%s, continuing without L3", chat_id)
                except Exception as exc:
                    log.warning("prefetch failed chat=%s: %s, continuing without L3", chat_id, exc)
            else:
                log.debug("prefetch skipped (low confidence %.2f) chat=%s", confidence, chat_id)
            collector.end_stage("search")
            collector.set_cache(cache_hit)
            collector.set_prefetch(prefetch_attempted, prefetch_succeeded, prefetch_timeout, len(l3_results), 0)
            # 3. Assemble L0+L1+L2+L3 (assemble stage); derive tokens/results.
            collector.start_stage("assemble")
            context_blocks, l3_used = await layers.assemble_context(chat_id, budget=budget, l3_results=l3_results)
            collector.end_stage("assemble")
            collector.set_context_from_blocks(context_blocks, results_found=len(l3_results), results_used=l3_used)
            # 4-5. Build prompt + LLM call + tool round (inject stage).
            collector.start_stage("inject")
            system_content = "\n\n".join(context_blocks)
            if self._has_search_memory():
                system_content += f"\n\n{_SEARCH_MEMORY_GUIDANCE}"
            if self._has_store_memory():
                system_content += f"\n\n{_STORE_MEMORY_GUIDANCE}"
            api_msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": intent},
            ]
            kw: dict = dict(model=settings.MODEL_NAME, messages=api_msgs,
                            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS)
            if self._tools:
                kw["tools"] = [t.to_openai_schema() for t in self._tools]
                kw["tool_choice"] = "auto"
            response = await self._registry.llm_client.chat.completions.create(**kw)
            choice = response.choices[0]
            if choice.message.tool_calls:
                reply = await self._run_tool_round(api_msgs, choice, chat_id)
            else:
                reply = choice.message.content or ""
            # 6. Persist this turn: reload history, append user + assistant, save (L2).
            messages = await layers.get_working_context(chat_id)
            messages.append({"role": "user", "content": intent, "timestamp": time.time()})
            messages.append({"role": "assistant", "content": reply, "timestamp": time.time()})
            await layers.save_working_context(chat_id, messages)
            collector.end_stage("inject")
            # 7. Record the observation; log a report at the configured interval.
            collector.finish(time.time() - start)
            analytics.record(collector.obs)
            if analytics.total_requests % ANALYTICS_LOG_INTERVAL == 0:
                analytics.log_report()
            return reply
        except Exception:
            log.warning("run() failed chat=%s, recording partial observation", chat_id)
            collector.finish(time.time() - start)
            analytics.record(collector.obs)
            raise

    async def _run_tool_round(self, api_msgs: list, choice, chat_id: str) -> str:
        """Execute tool calls from choice, make one more LLM call (no tools), return text.

        ``chat_id`` is threaded through to each handler so tools that need an
        origin chat (e.g. ``store_memory``) receive it without the model ever
        having to supply it. It is a per-call parameter, not stored on the
        orchestrator, so concurrent ``run()`` calls don't clobber each other.
        """
        tool_exchange = [
            {"role": "assistant", "content": choice.message.content,
             "tool_calls": [
                 {"id": tc.id, "type": "function",
                  "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                 for tc in choice.message.tool_calls
             ]}
        ]
        for tc in choice.message.tool_calls:
            result = await self._call_tool(tc, chat_id)
            tool_exchange.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        r2 = await self._registry.llm_client.chat.completions.create(
            model=settings.MODEL_NAME, messages=api_msgs + tool_exchange,
            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
        )
        return r2.choices[0].message.content or ""

    async def _call_tool(self, tc, chat_id: str) -> str:
        """Dispatch a tool call. Returns str(result) or JSON {"error": ...} on failure.

        The current ``chat_id`` is injected into the handler kwargs so tools
        that need an origin chat receive it; the model's arguments are passed
        through unchanged. Handlers accept ``chat_id`` (those that don't need
        it simply ignore it).
        """
        handler = next((t for t in self._tools if t.name == tc.function.name), None)
        if handler is None:
            return json.dumps({"error": f"unknown tool: {tc.function.name}"})
        try:
            args = json.loads(tc.function.arguments)
            args["chat_id"] = chat_id
            return str(await handler.handler(**args))
        except Exception as exc:
            log.warning("Tool %s raised: %s", tc.function.name, exc)
            return json.dumps({"error": str(exc)})