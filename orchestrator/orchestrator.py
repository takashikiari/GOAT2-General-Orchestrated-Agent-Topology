"""orchestrator.orchestrator — stateless LLM driver with AITS budgeting, async prefetch, single-round tool calling, and per-request observability."""
from __future__ import annotations

import asyncio
import json
import re
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

# deepseek-v4-flash (and similar models) sometimes return tool call intent as
# DSML markup in content instead of the standard OpenAI tool_calls field.
# Two observed formats:
#   - JSON body:      <｜｜DSML｜｜invoke name="tool">{"key": val}</｜｜DSML｜｜invoke>
#   - parameter tags: <｜｜DSML｜｜invoke name="tool"><｜｜DSML｜｜parameter name="k" ...>v</...>
_DSML_BLOCK = re.compile(
    r"<｜｜DSML｜｜tool_calls>(.*?)</｜｜DSML｜｜tool_calls>", re.DOTALL
)
_DSML_INVOKE = re.compile(
    r'<｜｜DSML｜｜invoke name="([^"]+)">(.*?)</｜｜DSML｜｜invoke>', re.DOTALL
)
_DSML_PARAM = re.compile(
    r'<｜｜DSML｜｜parameter name="([^"]+)"\s+string="([^"]*)"[^>]*>(.*?)</｜｜DSML｜｜parameter>',
    re.DOTALL,
)

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

# Guidance appended when the promote_memory tool is configured, so GOAT knows it
# can promote a STABLE fact into permanent core-memory (L1) — distinct from
# store_memory (episodic/L3). A constant (content), like _STORE_MEMORY_GUIDANCE.
_PROMOTE_MEMORY_GUIDANCE = (
    "For stable, reusable facts worth keeping in context for EVERY future "
    "session (e.g. the user's name, role, a long-term preference), use "
    "promote_memory to write permanent core-memory. Use store_memory for "
    "ordinary episodic context; reserve promote_memory for the few facts that "
    "should never fall out of context."
)

# Guidance appended when introspection plugins are configured, so GOAT knows
# it can inspect its own live logs and memory metrics on demand.
_INTROSPECTION_GUIDANCE = (
    "If the user asks about your own logs, memory metrics, cache hit rate, "
    "latency, or how your memory is doing, use the get_memory_metrics and "
    "get_recent_logs tools to inspect your live state before answering."
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

    def _all_tools(self) -> list[ToolDefinition]:
        """Core tools plus the live plugin tools (re-read each turn)."""
        return [*self._tools, *self._registry.plugin_manager.tools]

    def _has_tool(self, name: str) -> bool:
        """True when a tool named ``name`` (core or plugin) is configured."""
        return any(t.name == name for t in self._all_tools())

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
            # 2. Unconditional L3 search (search stage, non-blocking, bounded time).
            #    Pre-search confidence gating is removed — every turn searches;
            #    relevance is decided post-search by similarity score (assemble),
            #    not by query form. The search_memory tool remains the on-demand
            #    fallback. latency_search (~0.2s, local embedding) is negligible
            #    next to the LLM call in the inject/llm stage.
            collector.start_stage("search")
            l3_results: list[dict] = []
            cache_hit = False
            cache_key: str | None = None
            prefetch_attempted = True
            prefetch_succeeded = False
            prefetch_timeout = False
            try:
                l3_results, cache_hit, cache_key = await asyncio.wait_for(
                    layers.search_episodic_with_cache(chat_id, intent, limit=MAX_RESULTS_PER_SEARCH),
                    timeout=PREFETCH_TIMEOUT,
                )
                prefetch_succeeded = True
                log.info("episodic search ok chat=%s hits=%d", chat_id, len(l3_results))
            except asyncio.TimeoutError:
                prefetch_timeout = True
                log.warning("episodic search timed out chat=%s, continuing without L3", chat_id)
            except Exception as exc:
                log.warning("episodic search failed chat=%s: %s, continuing without L3", chat_id, exc)
            collector.end_stage("search")
            collector.set_cache(cache_hit, cache_key)
            collector.set_prefetch(prefetch_attempted, prefetch_succeeded, prefetch_timeout, len(l3_results), 0)
            # 3. Assemble L0+L1+L2+L3 (assemble stage); derive tokens/results.
            collector.start_stage("assemble")
            context_blocks, l3_used = await layers.assemble_context(chat_id, budget=budget, l3_results=l3_results)
            collector.end_stage("assemble")
            collector.set_context_from_blocks(context_blocks, results_found=len(l3_results), results_used=l3_used)
            collector.set_prefetch_blocks_used(l3_used)
            # 4. Build the prompt (inject stage — small, just assembly).
            collector.start_stage("inject")
            system_content = "\n\n".join(context_blocks)
            if self._has_search_memory():
                system_content += f"\n\n{_SEARCH_MEMORY_GUIDANCE}"
            if self._has_store_memory():
                system_content += f"\n\n{_STORE_MEMORY_GUIDANCE}"
            if self._has_tool("promote_memory"):
                system_content += f"\n\n{_PROMOTE_MEMORY_GUIDANCE}"
            if self._has_tool("get_memory_metrics") or self._has_tool("get_recent_logs"):
                system_content += f"\n\n{_INTROSPECTION_GUIDANCE}"
            api_msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": intent},
            ]
            all_tools = self._all_tools()
            kw: dict = dict(model=settings.MODEL_NAME, messages=api_msgs,
                            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS)
            if all_tools:
                kw["tools"] = [t.to_openai_schema() for t in all_tools]
                kw["tool_choice"] = "auto"
            collector.end_stage("inject")
            # 5. LLM call + tool round (llm stage — the dominant latency).
            collector.start_stage("llm")
            response = await self._registry.llm_client.chat.completions.create(**kw)
            choice = response.choices[0]
            content = choice.message.content or ""
            log.debug(
                "LLM response chat=%s tool_calls=%s content_hex=%s",
                chat_id,
                bool(choice.message.tool_calls),
                content[:80].encode("unicode_escape").decode(),
            )
            if choice.message.tool_calls:
                reply = await self._run_tool_round(api_msgs, choice, chat_id, all_tools)
                if _DSML_BLOCK.search(reply):
                    log.warning("DSML in _run_tool_round reply chat=%s; running DSML round", chat_id)
                    reply = await self._run_dsml_tool_round(reply, chat_id, all_tools)
            elif _DSML_BLOCK.search(content):
                log.warning("DSML tool calls in content (model=%s); running DSML round", settings.MODEL_NAME)
                reply = await self._run_dsml_tool_round(content, chat_id, all_tools)
            else:
                reply = content
            collector.end_stage("llm")
            # 6. Persist this turn: reload history, append user + assistant, save (L2).
            collector.start_stage("save")
            messages = await layers.get_working_context(chat_id)
            messages.append({"role": "user", "content": intent, "timestamp": time.time()})
            messages.append({"role": "assistant", "content": reply, "timestamp": time.time()})
            await layers.save_working_context(chat_id, messages)
            collector.end_stage("save")
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

    async def _run_tool_round(self, api_msgs: list, choice, chat_id: str,
                              tools: list[ToolDefinition]) -> str:
        """Execute tool calls from choice, make one more LLM call (no tools), return text.

        ``chat_id`` is threaded through to each handler so tools that need an
        origin chat (e.g. ``store_memory``) receive it without the model ever
        having to supply it. It is a per-call parameter, not stored on the
        orchestrator, so concurrent ``run()`` calls don't clobber each other.
        ``tools`` is the snapshot of core+plugin tools the model was offered
        this turn; dispatch is resolved against it so a mid-turn reload can't
        drop a tool the model already called.
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
            result = await self._call_tool(tc, chat_id, tools)
            tool_exchange.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        r2 = await self._registry.llm_client.chat.completions.create(
            model=settings.MODEL_NAME, messages=api_msgs + tool_exchange,
            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
        )
        r2_content = r2.choices[0].message.content or ""
        log.debug("_run_tool_round r2 content_hex=%s", r2_content[:80].encode("unicode_escape").decode())
        return r2_content

    async def _run_dsml_tool_round(self, content: str, chat_id: str,
                                   tools: list[ToolDefinition]) -> str:
        """Handle DSML-format tool calls (deepseek-v4-flash fallback path).

        Parses DSML markup from content, executes each tool, returns the raw
        tool output directly — no second LLM call.
        """
        invocations = _DSML_INVOKE.findall(content)
        if not invocations:
            log.warning("DSML block detected but no parseable invocations chat=%s", chat_id)
            return ""
        results: list[str] = []
        for name, raw_args in invocations:
            stripped = raw_args.strip()
            try:
                args = json.loads(stripped) if stripped else {}
            except json.JSONDecodeError:
                # parameter-tag format: <｜｜DSML｜｜parameter name="k" string="bool">v</>
                args = {}
                for param_name, is_string, raw_val in _DSML_PARAM.findall(stripped):
                    val: object = raw_val.strip()
                    if is_string.lower() != "true":
                        try:
                            val = json.loads(str(val))
                        except (json.JSONDecodeError, ValueError):
                            pass
                    args[param_name] = val
            handler = next((t for t in tools if t.name == name), None)
            if handler is None:
                results.append(json.dumps({"error": f"unknown tool: {name}"}))
            else:
                try:
                    args["chat_id"] = chat_id
                    results.append(str(await handler.handler(**args)))
                except Exception as exc:
                    log.warning("DSML tool %s raised: %s", name, exc)
                    results.append(json.dumps({"error": str(exc)}))
        return "\n\n".join(results)

    async def _call_tool(self, tc, chat_id: str, tools: list[ToolDefinition]) -> str:
        """Dispatch a tool call. Returns str(result) or JSON {"error": ...} on failure.

        The current ``chat_id`` is injected into the handler kwargs so tools
        that need an origin chat receive it; the model's arguments are passed
        through unchanged. Handlers accept ``chat_id`` (those that don't need
        it simply ignore it). ``tools`` is this turn's core+plugin snapshot.
        """
        handler = next((t for t in tools if t.name == tc.function.name), None)
        if handler is None:
            return json.dumps({"error": f"unknown tool: {tc.function.name}"})
        try:
            args = json.loads(tc.function.arguments)
            args["chat_id"] = chat_id
            return str(await handler.handler(**args))
        except Exception as exc:
            log.warning("Tool %s raised: %s", tc.function.name, exc)
            return json.dumps({"error": str(exc)})