"""orchestrator.orchestrator — stateless LLM driver with AITS budgeting, async prefetch, single-round tool calling, and per-request observability."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Callable

from config import settings
from memory.activation import classify_turn, classify_write, find_topic_return, rescore_recency
from memory.aits import (
    calculate_complexity_from_query,
    calculate_confidence_from_query,
    calculate_intent_budget,
)
from memory.analytics import MemoryAnalytics
from memory.budget import enforce_result_limit
from memory.config import (
    ANALYTICS_LOG_INTERVAL,
    PREFETCH_MAX_RESULTS,
    TOPIC_RETURN_THRESHOLD,
)
from memory.layers import MemoryLayers
from memory.observability_collector import ObservationCollector
from memory.result_merger import merge_results
from memory.retrieval import retrieve, temporal_candidates
from memory.temporal_route import parse_interval
from orchestrator.prefetch import run_prefetch_and_save
from orchestrator.tool_loop_config import (
    AGENTIC_MAX_ITERATIONS,
    TOOL_ARGS_PREVIEW_CHARS,
    TOOL_RESULT_HEAD_CHARS,
    TOOL_RESULT_SHORT_THRESHOLD,
    TOOL_RESULT_TAIL_CHARS,
    TOOL_ROUND_MAX_OUTPUT_CHARS,
)
from orchestrator.tools import ToolDefinition
from plugins.plugin_manager import PluginManager
from utils.logging.setup import get_logger

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

# When DSML markup shows up AFTER the structured tool-calls loop already hit
# its cap (the model refusing to stop even once tools were withheld), the
# DSML round gets a small dedicated budget, not a second full
# AGENTIC_MAX_ITERATIONS allowance — that would let a model alternate
# structured calls and DSML markup to roughly double its real ceiling on a
# live, cost-per-call Telegram turn. When DSML is the model's native style
# for the WHOLE turn (no structured tool_calls used at all), it gets the
# full budget instead, mirroring the structured-call loop it's standing in
# for — see the two _run_dsml_tool_round call sites in run().
_DSML_CONTINUATION_MAX_ITERATIONS = 3

# Guidance appended to the system prompt when the search_memory tool is
# configured, so GOAT knows to fetch L3 on demand rather than claim it doesn't
# recall something. A constant (content, not a tunable), like _BASE_IDENTITY.
_SEARCH_MEMORY_GUIDANCE = (
    "search_memory is a last resort — only call it when there is NO episodic memory "
    "context at all in this prompt. If memory results are already present above "
    "(marked [Context recuperat din istoric]), they are the complete prefetched result; "
    "do not call search_memory again. Trust the prefetch."
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

# Injected as the final user-turn in _run_tool_round, immediately before the
# second LLM call. Without this, the last message the model sees before
# generating is raw tool output — whose format dominates generation style
# because of context-position proximity, overriding the distant system-message
# persona. This is NOT redundant with L0/L1: those answer "who you are";
# this answers "what to do right now with the data above" — a gap L0/L1
# structurally cannot close from the system-message position.
# The anti-fabrication clause directly addresses the class of failure where
# the model invents counts/numbers not present in the tool output.
_TOOL_SYNTHESIS_BRIDGE = (
    "Respond to my request based on the tool results above. "
    "Only state specific numbers, counts, or names if they appear verbatim "
    "in the tool output — if a figure was not returned by the tools, "
    "say so explicitly rather than guessing."
)

# Tool-summary evidence constants — controls what _compact_tool_summary persists
# from each tool call into L2/L3. Without this, only the synthesized reply text
# is saved, leaving future turns with no real grounding for "did you actually
# call X?" — the model's prior narrative is indistinguishable from a hallucination.
#
# Preview strategy by tool type:
#   _LARGE_OUTPUT_TOOLS: store "[N chars]" only; URL/path in args is the evidence.
#   _HEAD_TAIL_TOOLS: head + tail when long; grep/wc counts appear at line-end.
#   others: head-only when long.
# Short results (≤ _RESULT_SHORT_THRESHOLD) always stored in full regardless of type.
_LARGE_OUTPUT_TOOLS = frozenset({"browse_page", "fetch_content"})
_HEAD_TAIL_TOOLS = frozenset({"shell_run"})
_RESULT_SHORT_THRESHOLD = TOOL_RESULT_SHORT_THRESHOLD
_RESULT_HEAD = TOOL_RESULT_HEAD_CHARS
_RESULT_TAIL = TOOL_RESULT_TAIL_CHARS
_ARGS_MAX = TOOL_ARGS_PREVIEW_CHARS


def _compact_tool_summary(calls_and_results: list[tuple[str, str, str]]) -> str:
    """One evidence line per tool call: 'called {name}({args}) → {result_preview}'.

    Written into the saved assistant message so future turns can verify past
    tool use against real logged evidence, not just the model's prior narrative.
    """
    lines = []
    for name, args_json, result in calls_and_results:
        args = args_json if len(args_json) <= _ARGS_MAX else args_json[:_ARGS_MAX] + "..."
        if name in _LARGE_OUTPUT_TOOLS:
            preview = f"[{len(result)} chars]"
        elif len(result) <= _RESULT_SHORT_THRESHOLD:
            preview = result
        elif name in _HEAD_TAIL_TOOLS:
            preview = result[:_RESULT_HEAD] + "..." + result[-_RESULT_TAIL:]
        else:
            preview = result[:_RESULT_HEAD] + "..."
        lines.append(f"called {name}({args}) → {preview}")
    return "\n".join(lines)


async def _archive_turn(
    layers, chat_id: str, intent: str, reply: str,
    topic_id: str = "", doc_id: str | None = None,
) -> None:
    """Fire-and-forget: archive the user/reply pair into L3 episodic memory.

    ``reply`` is the clean synthesized text — callers must NOT pass a
    ``[Tool calls]``-prefixed string here; that evidence belongs in L2
    (working context) only, so raw tool output (find/ls/log dumps) never
    permanently pollutes the L3 corpus prefetch reads from.

    Tagged 'l2_full_archive'. ``topic_id`` links the entry to its topic thread.
    ``doc_id`` is pre-generated by the orchestrator so L2 messages carry an
    ``l3_id`` field before this async write completes.
    """
    try:
        content = f"user: {intent}\nassistant: {reply}"
        await layers.store_episodic(
            chat_id, content, tags=["l2_full_archive"], topic_id=topic_id, doc_id=doc_id,
        )
        log.debug("L3 archive write ok: chat=%s topic=%s doc_id=%s", chat_id, topic_id, doc_id)
    except Exception as exc:
        log.warning("L3 archive dump failed chat=%s: %s", chat_id, exc)


class Orchestrator:
    """Stateless (intent, chat_id)→LLM→reply driver with AITS + observability.

    All memory access flows through ``layers`` (the Backend Mapper); the
    orchestrator never imports the physical tiers. Per turn it starts a prefetch
    daemon (the FIRST step, fire-and-forget) whose 3 mechanisms classify and
    search L3 in parallel with the L0/L1/L2 fetch, computes a dynamic AITS token
    budget (confidence + complexity), awaits the daemon under a bounded timeout
    (no confidence gate — every turn attempts prefetch), then assembles context
    via ``layers.assemble_context`` (L0+L1 protected, L2 protected to
    ``L2_CONTEXT_CAP``, L3 AITS-gated). One ``ObservationCollector`` records the
    turn (latency per stage, tokens per tier, cache/prefetch outcome) and feeds
    ``analytics``. Memory is the kernel's context only; tools and agents are
    separate systems, not budget inputs.
    """

    def __init__(
        self,
        layers: MemoryLayers,
        llm_client,
        plugin_manager: PluginManager,
        analytics: MemoryAnalytics,
        tools: list[ToolDefinition] | None = None,
    ) -> None:
        self._layers = layers
        self._llm = llm_client
        self._plugin_manager = plugin_manager
        self._analytics = analytics
        self._tools = tools or []
        # In-flight background tasks (L3 archive writes + prefetch background
        # saves) tracked so drain_background() can await them on shutdown.
        # Each task removes itself here on completion via done_callback.
        self._pending_bg: set[asyncio.Task] = set()

    async def drain_background(self, timeout: float = 5.0) -> None:
        """Await in-flight background tasks (L3 archive writes + prefetch saves).

        Called from the bot's ``post_shutdown`` hook before the loop exits.
        Bounded by ``timeout``; tasks already swallow+log their own errors.
        """
        pending = list(self._pending_bg)
        if not pending:
            return
        log.info("draining %d in-flight background tasks", len(pending))
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "background drain timed out (%.1fs); %d task(s) may be lost",
                timeout, len(self._pending_bg),
            )

    def _has_search_memory(self) -> bool:
        """True when the ``search_memory`` tool is configured for this orchestrator."""
        return any(t.name == "search_memory" for t in self._tools)

    def _has_store_memory(self) -> bool:
        """True when the ``store_memory`` tool is configured for this orchestrator."""
        return any(t.name == "store_memory" for t in self._tools)

    def _all_tools(self) -> list[ToolDefinition]:
        """Core tools plus the live plugin tools (re-read each turn)."""
        return [*self._tools, *self._plugin_manager.tools]

    def _has_tool(self, name: str) -> bool:
        """True when a tool named ``name`` (core or plugin) is configured."""
        return any(t.name == name for t in self._all_tools())

    async def run(
        self, intent: str, chat_id: str, *,
        on_context_assembled: Callable[[list[str]], None] | None = None,
        on_tool_summary: Callable[[str], None] | None = None,
    ) -> str:
        """Single turn: AITS budget → bounded-time L3 prefetch → assemble → LLM → save.

        Each stage is timed (classify / search / assemble / inject) by an
        ``ObservationCollector``; on success the observation is recorded into
        ``memory_analytics`` and a report is logged every
        ``ANALYTICS_LOG_INTERVAL`` requests. On exception the partial
        observation is recorded before re-raising. The LLM call + tool round
        fall inside the ``inject`` stage (the biggest latency). The tool round
        is an agentic loop (``_run_tool_round``): the model is called WITH tools
        each iteration so it can chain tools across the turn, and WITHOUT tools
        only at the ``AGENTIC_MAX_ITERATIONS`` cap so a stuck model must
        synthesize. Runaway loops are bounded by the cap, not structurally
        impossible.

        ``on_context_assembled``, if given, is called once with the exact
        ``context_blocks`` assembled this turn — a side channel for callers
        (the benchmark's groundedness judge) that need the raw retrieved text,
        kept separate from ``MemoryObservation`` which stays privacy-truncated
        and unconditionally logged.

        ``on_tool_summary``, if given, is called once with the turn's
        ``tool_summary`` (``""`` when no tools were used) — the same side
        channel for callers that need tool-call evidence, e.g. the
        benchmark's groundedness judge, which otherwise only sees memory
        context and misjudges a correct tool-sourced answer as unsupported.
        """
        layers = self._layers
        analytics = self._analytics
        collector = ObservationCollector(chat_id, intent)
        start = time.time()
        try:
            # 1. Kick off the L0/L1/L2 tier fetch concurrently with the activation
            #    lookup + query embed. The activation (L2.5) holds the L3 results
            #    pre-fetched by the PREVIOUS turn's post-turn daemon — instant, no
            #    ChromaDB/BM25/GLiNER/CrossEncoder this turn.
            activation, query_emb = await asyncio.gather(
                layers.get_activation(chat_id),
                layers.embed_query(intent),
            )
            turn_state = classify_turn(intent, activation, query_emb)
            # Pre-compute topic_id and topic_return_id (pure CPU, no I/O) so that
            # _archive_turn and the post-turn prefetch use the same consistent value.
            topic_return_id: str | None = None
            if turn_state == "cold" and activation and activation.archived_topics:
                topic_return_id = find_topic_return(
                    query_emb, activation.archived_topics, TOPIC_RETURN_THRESHOLD,
                )
            if turn_state in ("warm", "drift") and activation and activation.topic_id:
                current_topic_id = activation.topic_id
            else:
                current_topic_id = topic_return_id or str(uuid.uuid4())
            # 2. AITS dynamic budget (classify stage) — CPU, instant.
            collector.start_stage("classify")
            confidence = calculate_confidence_from_query(intent)
            complexity = calculate_complexity_from_query(intent)
            budget = calculate_intent_budget(confidence, complexity)
            collector.set_confidence(confidence)
            collector.set_complexity(complexity)
            collector.set_intent(collector.categorize_intent(confidence))
            collector.set_budget(budget, 0)
            collector.end_stage("classify")
            log.info(
                "AITS budget=%d confidence=%.2f complexity=%.2f chat=%s",
                budget, confidence, complexity, chat_id,
            )
            # 3. Get L3. Warm: serve activation.merged (held from a previous
            #    turn on the SAME thread — still valid, no search needed).
            #    Cold/drift: activation.merged was computed for a DIFFERENT
            #    query (a topic break or a moved-on thread) — reusing it here
            #    would serve stale, likely-irrelevant context to THIS turn's
            #    LLM call, silently, forever (the model never re-checks it).
            #    Confirmed on real production log (2026-07-12): a topic-shift
            #    turn got no relevant context this way; the correct memory
            #    only surfaced a turn later, once the user had already moved
            #    on. So cold/drift must search NOW, on the current query —
            #    fired as a task (not awaited yet) so it overlaps with the
            #    L0/L1/L2 fetch below instead of stacking sequentially.
            #    Warm turns alone stay at zero added latency.
            collector.start_stage("search")
            l3_task: asyncio.Task | None = None
            l3_results: list[dict] = []
            warm_served = False
            if turn_state == "warm":
                if activation and activation.merged:
                    l3_results = rescore_recency(activation.merged, time.time())
                    warm_served = bool(l3_results)
            else:
                l3_task = asyncio.create_task(
                    retrieve(layers, chat_id, intent, turn_state, activation, topic_return_id)
                )
            # temporal_center: parse_interval is pure/cheap (regex + dateparser,
            # no embedding model, no network) — computed unconditionally so
            # assemble_context can always prioritize content near the moment
            # actually asked about, regardless of turn_state.
            #
            # The EXTRA synchronous search-and-merge below only runs on WARM
            # turns though: on cold/drift, retrieve() above already runs
            # temporal_candidates internally (memory/retrieval.py::_cold/
            # _drift), so doing it again here would just duplicate that
            # search. Warm turns' activation.merged carries no fresh search at
            # all, so an explicit date/time mention mid-thread still needs
            # this dedicated cheap fast-path — a single indexed ChromaDB
            # metadata-range query, not the full BM25 + dual-semantic +
            # entity-extraction pipeline.
            temporal_interval = parse_interval(intent)
            temporal_center: float | None = None
            if temporal_interval is not None:
                temporal_center = (temporal_interval[0] + temporal_interval[1]) / 2
                if turn_state == "warm":
                    fresh_temporal = await temporal_candidates(layers, intent, interval=temporal_interval)
                    if fresh_temporal:
                        # Rerank on this small candidate pool is cheap and worth
                        # it for quality; boost_by_entities/BM25 are skipped —
                        # those need GLiNER entity extraction, which this
                        # fast-path is explicitly avoiding to stay cheap.
                        groups = ([("warm", l3_results)] if l3_results else []) + [("temporal", fresh_temporal)]
                        combined = merge_results(groups)[:PREFETCH_MAX_RESULTS * 2]
                        l3_results = (await layers.rerank(intent, combined))[:PREFETCH_MAX_RESULTS]
                        log.info(
                            "temporal fast-path chat=%s found=%d merged_total=%d",
                            chat_id, len(fresh_temporal), len(l3_results),
                        )
            collector.end_stage("search")
            # 4. Fetch L0/L1/L2 concurrently (all fast — no heavy search I/O),
            #    joined by the in-flight cold/drift retrieve() task above so
            #    its search latency overlaps with these fetches instead of
            #    adding on top of them.
            collector.start_stage("assemble")
            gather_targets: list = [
                layers.get_identity_and_facts(),
                layers.get_identity_prompt(),
                layers.get_working_context(chat_id),
            ]
            if l3_task is not None:
                gather_targets.append(l3_task)
            gathered = await asyncio.gather(*gather_targets)
            facts, identity_prompt, messages = gathered[:3]
            if l3_task is not None:
                l3_results, _, _, _ = gathered[3]
            served = bool(l3_results)
            collector.set_cache(False, None)
            collector.set_prefetch(True, served, False, len(l3_results), 0)
            collector.set_prefetch_mechanisms(warm_served, len(l3_results), 0)
            context_blocks, l3_used = await layers.assemble_context(
                chat_id, budget=budget, l3_results=l3_results,
                facts=facts, messages=messages,
                identity_prompt=identity_prompt,
                temporal_center=temporal_center,
            )
            collector.end_stage("assemble")
            collector.set_context_from_blocks(context_blocks, results_found=len(l3_results), results_used=l3_used)
            collector.set_prefetch_blocks_used(l3_used)
            if on_context_assembled is not None:
                on_context_assembled(list(context_blocks))
            # 4. Build the prompt (inject stage — small, just assembly).
            collector.start_stage("inject")
            system_content = "\n\n".join(context_blocks)
            guidance_texts: list[str] = []
            if self._has_search_memory():
                guidance_texts.append(_SEARCH_MEMORY_GUIDANCE)
            if self._has_store_memory():
                guidance_texts.append(_STORE_MEMORY_GUIDANCE)
            if self._has_tool("promote_memory"):
                guidance_texts.append(_PROMOTE_MEMORY_GUIDANCE)
            if self._has_tool("get_memory_metrics") or self._has_tool("get_recent_logs"):
                guidance_texts.append(_INTROSPECTION_GUIDANCE)
            for guidance in guidance_texts:
                system_content += f"\n\n{guidance}"
            api_msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": intent},
            ]
            all_tools = self._all_tools()
            kw: dict = dict(model=settings.MODEL_NAME, messages=api_msgs,
                            temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS)
            tool_schemas: list[dict] = []
            if all_tools:
                tool_schemas = [t.to_openai_schema() for t in all_tools]
                kw["tools"] = tool_schemas
                kw["tool_choice"] = "auto"
            # Observability-only: fold the guidance text + tool schemas counted
            # above into tokens_injected so the metric reflects what actually
            # reaches the LLM (set_context_from_blocks only sees the three
            # assemble_blocks blocks, measured BEFORE either of these exist).
            # Does not change kw / api_msgs — same prompt sent either way.
            collector.add_prompt_extras("\n\n".join(guidance_texts), tool_schemas)
            collector.end_stage("inject")
            # 5. LLM call + tool round (llm stage — the dominant latency).
            collector.start_stage("llm")
            _t_first_start = time.time()
            response = await self._llm.chat.completions.create(**kw)
            _t_first = time.time() - _t_first_start
            choice = response.choices[0]
            content = choice.message.content or ""
            log.debug(
                "LLM response chat=%s tool_calls=%s content_hex=%s",
                chat_id,
                bool(choice.message.tool_calls),
                content[:80].encode("unicode_escape").decode(),
            )
            _u = getattr(response, "usage", None)
            _usage_prompt = (getattr(_u, "prompt_tokens", 0) or 0)
            _usage_completion = (getattr(_u, "completion_tokens", 0) or 0)
            _usage_total = (getattr(_u, "total_tokens", 0) or 0)
            _llm_calls = 1
            _t_tool_rounds = 0.0
            tool_summary = ""
            stored_contents: list[str] = []
            if choice.message.tool_calls:
                reply, tool_summary, stored_contents, round_usage = await self._run_tool_round(
                    api_msgs, choice, chat_id, all_tools)
                _usage_prompt += round_usage["prompt_tokens"]
                _usage_completion += round_usage["completion_tokens"]
                _usage_total += round_usage["total_tokens"]
                _llm_calls += round_usage["calls"]
                _t_tool_rounds = round_usage["latency"]
                if _DSML_BLOCK.search(reply):
                    log.warning("DSML in _run_tool_round reply chat=%s; running DSML round", chat_id)
                    # Continuation budget, not a fresh full one — the structured
                    # loop already spent its allowance; see
                    # _DSML_CONTINUATION_MAX_ITERATIONS.
                    reply, dsml_summary, dsml_stored, dsml_usage = await self._run_dsml_tool_round(
                        reply, chat_id, all_tools, api_msgs,
                        max_iterations=_DSML_CONTINUATION_MAX_ITERATIONS,
                        chars_already_spent=round_usage["output_chars"],
                    )
                    if dsml_summary:
                        tool_summary = f"{tool_summary}\n{dsml_summary}".strip()
                    stored_contents.extend(dsml_stored)
                    _usage_prompt += dsml_usage["prompt_tokens"]
                    _usage_completion += dsml_usage["completion_tokens"]
                    _usage_total += dsml_usage["total_tokens"]
                    _llm_calls += dsml_usage["calls"]
                    _t_tool_rounds += dsml_usage["latency"]
            elif _DSML_BLOCK.search(content):
                log.warning("DSML tool calls in content (model=%s); running DSML round", settings.MODEL_NAME)
                # No structured tool_calls were used at all this turn — DSML
                # IS the primary loop here, so it gets the full budget.
                reply, tool_summary, stored_contents, dsml_usage = await self._run_dsml_tool_round(
                    content, chat_id, all_tools, api_msgs)
                _usage_prompt += dsml_usage["prompt_tokens"]
                _usage_completion += dsml_usage["completion_tokens"]
                _usage_total += dsml_usage["total_tokens"]
                _llm_calls += dsml_usage["calls"]
                _t_tool_rounds += dsml_usage["latency"]
            else:
                reply = content
            if on_tool_summary is not None:
                on_tool_summary(tool_summary)
            collector.end_stage("llm")
            collector.set_llm_usage(_usage_prompt, _usage_completion, _usage_total, _llm_calls)
            collector.set_llm_latency_breakdown(_t_first, _t_tool_rounds)
            # 6. Persist this turn: reload history, append user + assistant, save (L2).
            #    When tools were used, embed a compact evidence record in the assistant
            #    message so future turns have real grounding for past tool calls.
            #    Without this, only the synthesized reply text is saved — identical in
            #    structure to a hallucinated claim and indistinguishable under questioning.
            #    Also fire an async L3 archive write (non-blocking) — but L3 gets the
            #    CLEAN reply, not saved_reply: prod measurement (2026-07-09) found the
            #    longest, least-enriched L3 entries were almost all raw tool dumps
            #    (find/ls/get_recent_logs output) archived verbatim, forever, into the
            #    corpus prefetch reads from. L2 keeps the full [Tool calls] evidence for
            #    in-session grounding; only permanent L3 storage drops it. Trade-off: a
            #    NEW chat_id recalling "did you check X" no longer finds tool proof in
            #    L3, only the synthesized claim.
            collector.start_stage("save")
            saved_reply = f"[Tool calls]\n{tool_summary}\n\n{reply}" if tool_summary else reply
            now = time.time()
            l3_doc_id = str(uuid.uuid4())
            await layers.append_and_save_working_context(
                chat_id,
                {"role": "user", "content": intent, "timestamp": now, "l3_id": l3_doc_id},
                {"role": "assistant", "content": saved_reply, "timestamp": now, "l3_id": l3_doc_id},
            )
            archive_task = asyncio.create_task(
                _archive_turn(
                    layers, chat_id, intent, reply,
                    topic_id=current_topic_id,
                    doc_id=l3_doc_id,
                ))
            self._pending_bg.add(archive_task)
            archive_task.add_done_callback(self._pending_bg.discard)
            collector.end_stage("save")
            # 7. Enriching-write refresh (synchronous, end of turn): if GOAT stored
            #    on-thread facts this turn, fold them into the activation NOW so
            #    the next turn sees them — the ordering invariant (a brain folds
            #    new learning in before it speaks again). Off-thread/filing writes
            #    and no-write turns add no latency.
            write_kind = "none"
            enriching_refresh = False
            if stored_contents and activation is not None and activation.centroid:
                write_kind, enriching_refresh = await self._enriching_refresh(
                    layers, chat_id, stored_contents, activation)
            collector.set_activation(
                turn_state,
                thread_break=(turn_state == "cold" and activation is not None),
                write_kind=write_kind,
                enriching_refresh=enriching_refresh,
            )
            # 8. Post-turn prefetch: pre-compute L3 for the next turn in the
            #    inter-turn gap. No timeout — has as long as it needs before
            #    the user sends their next message. turn_start=start (this
            #    turn's ORIGIN time, captured before any I/O) travels all the
            #    way to ActivationStore.set as the write-race ordering key —
            #    if this turn's prefetch is slow and a later turn's faster
            #    prefetch already wrote, this write must lose, not clobber it.
            # turn_state != "warm" means retrieve() already ran synchronously
            # above (step 3) for THIS turn's query — pass its result through so
            # the daemon doesn't re-run the same search a second time; it only
            # needs to persist it into activation for the next turn to read.
            _prefetch_bg = asyncio.create_task(
                run_prefetch_and_save(
                    layers, chat_id, intent, query_emb, turn_state, activation,
                    topic_return_id=topic_return_id, forced_topic_id=current_topic_id,
                    turn_start=start,
                    precomputed_l3=l3_results if turn_state != "warm" else None,
                ))
            self._pending_bg.add(_prefetch_bg)
            _prefetch_bg.add_done_callback(self._pending_bg.discard)
            # 9. Record the observation; log a report at the configured interval.
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

    async def _enriching_refresh(
        self, layers, chat_id: str, stored_contents: list[str], activation,
    ) -> tuple[str, bool]:
        """Fold on-thread (enriching) writes into the activation before returning.

        For each ``store_memory`` content this turn, classify it against the
        current thread's centroid. If enriching (on-thread), synchronously
        re-search the thread's ``last_query`` (uncached, fresh — the just-written
        fact is now in L3) and replace the activation's results, so the next turn
        sees the new learning immediately (the ordering invariant). Filing
        (off-thread) writes leave the activation untouched — they surface when a
        future thread about that topic activates. Returns ``(write_kind, refreshed)``.
        """
        for content in stored_contents:
            cemb = await layers.embed_query(content)
            if classify_write(cemb, activation.centroid) == "enriching":
                fresh = enforce_result_limit(await layers.search_episodic(
                    activation.last_query, limit=PREFETCH_MAX_RESULTS))
                activation.merged = merge_results([("refresh", fresh)])[:PREFETCH_MAX_RESULTS]
                await layers.set_activation(chat_id, activation)
                log.info("enriching refresh chat=%s folded %d chars", chat_id, len(content))
                return "enriching", True
        return "filing", False

    async def _run_tool_round(self, api_msgs: list, choice, chat_id: str,
                              tools: list[ToolDefinition]) -> tuple[str, str, list[str], dict]:
        """Execute tool calls, loop the model until it stops calling tools or the
        iteration cap forces synthesis; return ``(reply, tool_summary, stored)``.

        Agentic loop, the same shape Claude Code runs: the model chains tools in
        one turn — read → search → write → verify → synthesize — in whatever order
        the query needs. Each iteration executes the model's pending
        ``tool_calls``, folds the assistant tool_calls + tool results into the
        running conversation, and calls the model again. Below
        ``AGENTIC_MAX_ITERATIONS`` it is called WITH tools (it decides: another
        tool, or a plain-text reply = natural termination); at the cap it is
        called WITHOUT tools so a stuck model must synthesize from what it has
        gathered.

        The cap is a HARD backstop, NOT a grounding decider — it never inspects
        content or withdraws a claim (that was the harmful regex-detector path,
        now removed). It only bounds cost/latency per turn (each iteration is one
        paid LLM call; worst case = 1 + ``AGENTIC_MAX_ITERATIONS`` calls) and
        prevents a runaway loop from hanging a single Telegram turn. The
        ``_TOOL_SYNTHESIS_BRIDGE`` is appended before every post-tool call as
        in-context support — a nudge, not a decision; the model stays decisional
        and may call another tool instead of answering.

        Returns ``(reply, tool_summary, stored_contents)`` where ``tool_summary``
        (built by ``_compact_tool_summary``) covers EVERY tool call across all
        iterations, and ``stored_contents`` is every ``store_memory`` content
        this turn — captured here (not in ``_call_tool``) so the enriching-write
        refresh can classify them against the current thread after the round
        completes. It excludes the automatic ``_archive_turn`` write, which
        bypasses the tool.

        ``chat_id`` is threaded through to each handler so tools that need an
        origin chat (e.g. ``store_memory``) receive it without the model ever
        having to supply it. It is a per-call parameter, not stored on the
        orchestrator, so concurrent ``run()`` calls don't clobber each other.
        ``tools`` is the snapshot of core+plugin tools the model was offered
        this turn; dispatch is resolved against it so a mid-turn reload can't
        drop a tool the model already called.
        """
        calls_and_results: list[tuple[str, str, str]] = []
        stored_contents: list[str] = []
        loop_msgs = list(api_msgs)
        current = choice
        reply = ""
        _round_prompt = 0
        _round_completion = 0
        _round_total = 0
        _round_calls = 0
        _round_output_chars = 0
        _t_rounds_start = time.time()
        for iteration in range(AGENTIC_MAX_ITERATIONS):
            # current.message carries tool_calls — execute them and fold the
            # assistant tool_calls + tool results into the running conversation.
            tool_exchange = [
                {"role": "assistant", "content": current.message.content,
                 "tool_calls": [
                     {"id": tc.id, "type": "function",
                      "function": {"name": tc.function.name,
                                   "arguments": tc.function.arguments}}
                     for tc in current.message.tool_calls
                 ]}
            ]
            for tc in current.message.tool_calls:
                result = await self._call_tool(tc, chat_id, tools)
                if tc.function.name == "store_memory":
                    stored_contents.append(self._extract_content(tc.function.arguments))
                log.debug(
                    "tool_result tool=%s chat=%s iter=%d content=%r",
                    tc.function.name, chat_id, iteration, result,
                )
                tool_exchange.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": result})
                calls_and_results.append((tc.function.name, tc.function.arguments, result))
                _round_output_chars += len(result)
            loop_msgs = loop_msgs + tool_exchange
            # In-context grounding support, applied every iteration. Below the
            # cap the model is offered tools so it may chain another tool instead
            # of answering (decisional); at the cap tools are withheld so it must
            # synthesize from what it has.
            loop_msgs.append({"role": "user", "content": _TOOL_SYNTHESIS_BRIDGE})
            # Two independent hard backstops: round COUNT (AGENTIC_MAX_ITERATIONS)
            # and cumulative tool-output SIZE (TOOL_ROUND_MAX_OUTPUT_CHARS). Each
            # iteration resends the whole growing conversation, so a model issuing
            # a few large read_file/shell_run calls can blow the resent history
            # past the model's context window well before the iteration count
            # cap fires — the size cap catches that case independently.
            size_cap_hit = _round_output_chars > TOOL_ROUND_MAX_OUTPUT_CHARS
            if iteration + 1 < AGENTIC_MAX_ITERATIONS and not size_cap_hit:
                r = await self._llm.chat.completions.create(
                    model=settings.MODEL_NAME, messages=loop_msgs,
                    temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
                    tools=[t.to_openai_schema() for t in tools], tool_choice="auto",
                )
                _round_calls += 1
                _ru = getattr(r, "usage", None)
                if _ru is not None:
                    _round_prompt += getattr(_ru, "prompt_tokens", 0) or 0
                    _round_completion += getattr(_ru, "completion_tokens", 0) or 0
                    _round_total += getattr(_ru, "total_tokens", 0) or 0
                current = r.choices[0]
                if not current.message.tool_calls:
                    # Natural termination — the model chose to answer with text.
                    reply = current.message.content or ""
                    log.debug("tool_round natural-terminate chat=%s iters=%d",
                              chat_id, iteration + 1)
                    break
            else:
                # Cap reached — force synthesis (no tools). Hard backstop.
                r = await self._llm.chat.completions.create(
                    model=settings.MODEL_NAME, messages=loop_msgs,
                    temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
                )
                _round_calls += 1
                _ru = getattr(r, "usage", None)
                if _ru is not None:
                    _round_prompt += getattr(_ru, "prompt_tokens", 0) or 0
                    _round_completion += getattr(_ru, "completion_tokens", 0) or 0
                    _round_total += getattr(_ru, "total_tokens", 0) or 0
                reply = r.choices[0].message.content or ""
                log.warning(
                    "tool_round cap-forced synthesis chat=%s iters=%d reason=%s output_chars=%d",
                    chat_id, iteration + 1, "size" if size_cap_hit else "iterations",
                    _round_output_chars,
                )
                break
        _t_rounds = time.time() - _t_rounds_start
        log.debug("_run_tool_round reply content_hex=%s",
                  reply[:80].encode("unicode_escape").decode())
        round_usage = {
            "prompt_tokens": _round_prompt,
            "completion_tokens": _round_completion,
            "total_tokens": _round_total,
            "calls": _round_calls,
            "latency": _t_rounds,
            "output_chars": _round_output_chars,
        }
        return reply, _compact_tool_summary(calls_and_results), stored_contents, round_usage

    @staticmethod
    def _extract_content(args_json: str) -> str:
        """Pull the ``content`` arg from a store_memory tool call.

        Used by the enriching-write refresh to classify what GOAT stored this
        turn against the current thread's centroid. Returns ``""`` on any parse
        failure (a malformed call classifies as filing, never breaks the turn).
        """
        try:
            return str(json.loads(args_json).get("content", ""))
        except (json.JSONDecodeError, AttributeError, TypeError):
            return ""

    async def _run_dsml_tool_round(
        self, content: str, chat_id: str,
        tools: list[ToolDefinition],
        api_msgs: list | None = None,
        *,
        max_iterations: int = AGENTIC_MAX_ITERATIONS,
        chars_already_spent: int = 0,
    ) -> tuple[str, str, list[str], dict]:
        """Handle DSML-format tool calls (deepseek-v4-flash fallback path).

        An agentic loop, the same shape as _run_tool_round — NOT a one-shot
        fallback. deepseek-v4-flash keeps its DSML habit even in a synthesis
        call made WITHOUT tools offered (it has no structured tool_calls
        field to use, so it fakes one in text instead of answering). A
        single execute-then-synthesize pass left that second DSML block
        completely unchecked: every OTHER reply-producing path in this file
        re-checks itself for DSML markup before returning, but this
        function's own synthesis reply did not — real production incident,
        raw DSML markup reaching the user's Telegram chat unexecuted.

        Bounded by ``max_iterations`` (round count) and
        ``TOOL_ROUND_MAX_OUTPUT_CHARS`` (cumulative output size, offset by
        ``chars_already_spent`` so a DSML round invoked after the structured
        loop already spent some of that budget doesn't get to double-count
        it) — same two independent backstops _run_tool_round uses, for the
        same reason. Absolute last resort: even after the cap forces a
        final reply, if it STILL contains DSML markup, it is stripped
        before returning — raw DSML must never reach the user, full stop.

        Returns (reply, tool_summary, stored_contents, round_usage) — same
        shape as _run_tool_round's return, so call sites can fold usage/cost
        uniformly.
        """
        calls_and_results: list[tuple[str, str, str]] = []
        stored_contents: list[str] = []
        _round_prompt = 0
        _round_completion = 0
        _round_total = 0
        _round_calls = 0
        _round_output_chars = 0
        _t_start = time.time()
        reply = ""

        for iteration in range(max_iterations):
            invocations = _DSML_INVOKE.findall(content)
            if not invocations:
                log.warning("DSML block detected but no parseable invocations chat=%s", chat_id)
                reply = ""
                break
            iter_results: list[tuple[str, str, str]] = []
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
                args_json = json.dumps(args)
                if handler is None:
                    result = json.dumps({"error": f"unknown tool: {name}"})
                else:
                    try:
                        args["chat_id"] = chat_id
                        result = str(await handler.handler(**args))
                    except Exception as exc:
                        log.warning("DSML tool %s raised: %s", name, exc)
                        result = json.dumps({"error": str(exc)})
                iter_results.append((name, args_json, result))
                _round_output_chars += len(result)
                if name == "store_memory":
                    stored_contents.append(self._extract_content(args_json))
            calls_and_results.extend(iter_results)
            raw = "\n\n".join(r for _, _, r in iter_results)

            if not api_msgs:
                # No conversation context available — return raw output as fallback.
                log.warning("DSML synthesis skipped (no api_msgs) chat=%s", chat_id)
                reply = raw
                break

            size_cap_hit = chars_already_spent + _round_output_chars > TOOL_ROUND_MAX_OUTPUT_CHARS
            # Synthesize: present the tool results to the model and ask it to
            # respond naturally, mirroring _run_tool_round's synthesis step.
            synth_msgs = list(api_msgs) + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Tool results:\n{raw}\n\n{_TOOL_SYNTHESIS_BRIDGE}"},
            ]
            try:
                r = await self._llm.chat.completions.create(
                    model=settings.MODEL_NAME, messages=synth_msgs,
                    temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
                )
                _round_calls += 1
                _ru = getattr(r, "usage", None)
                if _ru is not None:
                    _round_prompt += getattr(_ru, "prompt_tokens", 0) or 0
                    _round_completion += getattr(_ru, "completion_tokens", 0) or 0
                    _round_total += getattr(_ru, "total_tokens", 0) or 0
                synth_reply = r.choices[0].message.content or raw
            except Exception as exc:
                log.warning("DSML synthesis LLM call failed chat=%s: %s — returning raw", chat_id, exc)
                reply = raw
                break

            if not _DSML_BLOCK.search(synth_reply):
                reply = synth_reply
                break

            if iteration + 1 >= max_iterations or size_cap_hit:
                log.warning(
                    "dsml_round cap-forced reply chat=%s iters=%d reason=%s output_chars=%d",
                    chat_id, iteration + 1, "size" if size_cap_hit else "iterations",
                    _round_output_chars,
                )
                reply = synth_reply
                break

            log.warning(
                "DSML synthesis itself contained DSML chat=%s iter=%d; looping",
                chat_id, iteration + 1,
            )
            content = synth_reply

        # Absolute last resort — raw DSML markup must never reach the user,
        # no matter what the model does on the very last call.
        if _DSML_BLOCK.search(reply):
            log.warning("DSML markup survived the round cap chat=%s; stripping before reply", chat_id)
            reply = _DSML_BLOCK.sub("", reply).strip()
            if not reply:
                reply = (
                    "I gathered some information but couldn't put together a clean answer "
                    "yet — ask me to continue and I'll pick up where I left off."
                )

        round_usage = {
            "prompt_tokens": _round_prompt,
            "completion_tokens": _round_completion,
            "total_tokens": _round_total,
            "calls": _round_calls,
            "latency": time.time() - _t_start,
            "output_chars": _round_output_chars,
        }
        return reply, _compact_tool_summary(calls_and_results), stored_contents, round_usage

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
            result = str(await handler.handler(**args))
        except Exception as exc:
            log.warning("Tool %s raised: %s", tc.function.name, exc)
            return json.dumps({"error": str(exc)})
        # Defense in depth: individual plugins cap their OWN output (read_file's
        # max_chars, shell_run's 4KB cap), but a plugin that caps by a different
        # unit (get_recent_logs caps by line count, not chars) can still return
        # far more than the round budget when lines are long. One oversized
        # result is enough to blow the resent conversation past the model's
        # context window on the very next call, regardless of the cumulative
        # round-size backstop in _run_tool_round, which can only stop FUTURE
        # rounds — it can't shrink a result already produced. Truncate here so
        # no single call, from any tool, can ever exceed the round budget.
        if len(result) > TOOL_ROUND_MAX_OUTPUT_CHARS:
            omitted = len(result) - TOOL_ROUND_MAX_OUTPUT_CHARS
            log.warning(
                "Tool %s result truncated: %d chars omitted (cap=%d)",
                tc.function.name, omitted, TOOL_ROUND_MAX_OUTPUT_CHARS,
            )
            result = result[:TOOL_ROUND_MAX_OUTPUT_CHARS] + f"\n...[truncated {omitted} chars]"
        return result