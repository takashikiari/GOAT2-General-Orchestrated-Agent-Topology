"""orchestrator.orchestrator — stateless LLM driver with AITS budgeting, async prefetch, single-round tool calling, and per-request observability."""
from __future__ import annotations

import asyncio
import json
import re
import time

from config import settings
from memory.activation import Activation, classify_turn, classify_write, rescore_recency, trim_recent
from memory.aits import (
    calculate_complexity_from_query,
    calculate_confidence_from_query,
    calculate_intent_budget,
)
from memory.analytics import MemoryAnalytics
from memory.budget import enforce_result_limit
from memory.config import (
    AGENTIC_MAX_ITERATIONS,
    ANALYTICS_LOG_INTERVAL,
    PREFETCH_MAX_RESULTS,
    PREFETCH_TIMEOUT,
)
from memory.layers import MemoryLayers
from memory.observability_collector import ObservationCollector
from memory.query_classifier import extract_structural_keys, extract_temporal_range
from memory.result_merger import merge_results
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
_RESULT_SHORT_THRESHOLD = 400
_RESULT_HEAD = 200
_RESULT_TAIL = 150
_ARGS_MAX = 200


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


async def _archive_turn(layers, chat_id: str, intent: str, reply: str) -> None:
    """Fire-and-forget: archive the full message pair into L3 episodic memory.

    Tagged 'l2_full_archive' to distinguish raw archival writes from GOAT's
    curated store_memory/promote_memory calls. Never raises — L3 write failure
    must not affect the turn response.
    """
    try:
        content = f"user: {intent}\nassistant: {reply}"
        await layers.store_episodic(chat_id, content, tags=["l2_full_archive"])
        log.debug("L3 archive write ok: chat=%s", chat_id)
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
        # In-flight L3 archive writes, tracked so drain_archives() can await
        # them on shutdown — otherwise a restart in the ~100 ms ChromaDB write
        # window silently drops the turn (violating the "every turn archived"
        # guarantee). Each task removes itself here on completion.
        self._pending_archives: set[asyncio.Task] = set()

    async def drain_archives(self, timeout: float = 5.0) -> None:
        """Await in-flight L3 archive writes so a clean shutdown loses no turns.

        Called from the bot's ``post_shutdown`` hook before the loop exits.
        Bounded by ``timeout`` so a stuck ChromaDB write can't hang shutdown;
        ``_archive_turn`` already swallows+logs its own errors, so gather runs
        with ``return_exceptions=True`` purely defensively.
        """
        pending = list(self._pending_archives)
        if not pending:
            return
        log.info("draining %d in-flight L3 archive writes", len(pending))
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "archive drain timed out (%.1fs); %d write(s) may be lost",
                timeout, len(self._pending_archives),
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

    async def run(self, intent: str, chat_id: str) -> str:
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
        """
        layers = self._layers
        analytics = self._analytics
        collector = ObservationCollector(chat_id, intent)
        start = time.time()
        try:
            # 1. Kick off the L0/L1/L2 tier fetch concurrently with the activation
            #    lookup + query embed (all I/O-bound) so none blocks another. The
            #    activation (per-chat thread state) + query embedding decide the
            #    turn state: ``classify_turn`` returns cold / warm / drift, and the
            #    daemon skips the L3 search on a warm turn — the brain holding its
            #    current mental model steady instead of re-deriving it every turn.
            facts_task = asyncio.create_task(layers.get_identity_and_facts())
            msgs_task = asyncio.create_task(layers.get_working_context(chat_id))
            activation_task = asyncio.create_task(layers.get_activation(chat_id))
            qemb_task = asyncio.create_task(layers.embed_query(intent))
            activation = await activation_task
            query_emb = await qemb_task
            turn_state = classify_turn(intent, activation, query_emb)
            prefetch_task = asyncio.create_task(
                self._prefetch_daemon(chat_id, intent, turn_state, activation))
            # 3. AITS dynamic budget (classify stage) — CPU, instant.
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
            # 4. Await the daemon (search stage) — bounded; on timeout continue
            #    without L3. The cancelled task's mechanisms are torn down.
            collector.start_stage("search")
            l3_results: list[dict] = []
            cache_hit = False
            cache_key: str | None = None
            prefetch_attempted = True
            prefetch_succeeded = False
            prefetch_timeout = False
            _prefetch_meta: dict = {"warm_served": False, "thematic": 0, "temporal": 0, "specific_key": 0}
            try:
                l3_results, cache_hit, cache_key, _prefetch_meta = await asyncio.wait_for(
                    prefetch_task, timeout=PREFETCH_TIMEOUT,
                )
                prefetch_succeeded = True
                log.info("prefetch ok chat=%s state=%s hits=%d", chat_id, turn_state, len(l3_results))
            except asyncio.TimeoutError:
                prefetch_timeout = True
                prefetch_task.cancel()
                log.warning("prefetch timed out chat=%s, continuing without L3", chat_id)
            except Exception as exc:
                prefetch_task.cancel()
                log.warning("prefetch failed chat=%s: %s, continuing without L3", chat_id, exc)
            collector.end_stage("search")
            collector.set_cache(cache_hit, cache_key)
            collector.set_prefetch(prefetch_attempted, prefetch_succeeded, prefetch_timeout, len(l3_results), 0)
            collector.set_prefetch_mechanisms(
                _prefetch_meta["warm_served"],
                _prefetch_meta["thematic"],
                _prefetch_meta["temporal"],
                _prefetch_meta["specific_key"],
            )
            # Persist / refresh the activation (only on a successful prefetch; a
            # timeout leaves it untouched so the next turn re-derives cold).
            current_activation = None
            if prefetch_succeeded:
                current_activation = await self._update_activation(
                    layers, chat_id, intent, query_emb, turn_state, activation, l3_results)
            # 5. Await the concurrent L0/L1/L2 fetch (normally done already) and
            #    assemble L0-L3 with the pre-fetched tiers (assemble stage).
            collector.start_stage("assemble")
            facts = await facts_task
            messages = await msgs_task
            context_blocks, l3_used = await layers.assemble_context(
                chat_id, budget=budget, l3_results=l3_results,
                facts=facts, messages=messages,
            )
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
                    reply, dsml_summary, dsml_stored = await self._run_dsml_tool_round(
                        reply, chat_id, all_tools, api_msgs)
                    if dsml_summary:
                        tool_summary = f"{tool_summary}\n{dsml_summary}".strip()
                    stored_contents.extend(dsml_stored)
            elif _DSML_BLOCK.search(content):
                log.warning("DSML tool calls in content (model=%s); running DSML round", settings.MODEL_NAME)
                reply, tool_summary, stored_contents = await self._run_dsml_tool_round(
                    content, chat_id, all_tools, api_msgs)
            else:
                reply = content
            collector.end_stage("llm")
            collector.set_llm_usage(_usage_prompt, _usage_completion, _usage_total, _llm_calls)
            collector.set_llm_latency_breakdown(_t_first, _t_tool_rounds)
            # 6. Persist this turn: reload history, append user + assistant, save (L2).
            #    When tools were used, embed a compact evidence record in the assistant
            #    message so future turns have real grounding for past tool calls.
            #    Without this, only the synthesized reply text is saved — identical in
            #    structure to a hallucinated claim and indistinguishable under questioning.
            #    Also fire an async L3 archive write (full message pair, non-blocking).
            collector.start_stage("save")
            saved_reply = f"[Tool calls]\n{tool_summary}\n\n{reply}" if tool_summary else reply
            now = time.time()
            await layers.append_and_save_working_context(
                chat_id,
                {"role": "user", "content": intent, "timestamp": now},
                {"role": "assistant", "content": saved_reply, "timestamp": now},
            )
            archive_task = asyncio.create_task(_archive_turn(layers, chat_id, intent, saved_reply))
            self._pending_archives.add(archive_task)
            archive_task.add_done_callback(self._pending_archives.discard)
            collector.end_stage("save")
            # 7. Enriching-write refresh (synchronous, end of turn): if GOAT stored
            #    on-thread facts this turn, fold them into the activation NOW so
            #    the next turn sees them — the ordering invariant (a brain folds
            #    new learning in before it speaks again). Off-thread/filing writes
            #    and no-write turns add no latency.
            write_kind = "none"
            enriching_refresh = False
            if stored_contents and current_activation is not None and current_activation.centroid:
                write_kind, enriching_refresh = await self._enriching_refresh(
                    layers, chat_id, stored_contents, current_activation)
            collector.set_activation(
                turn_state,
                thread_break=(turn_state == "cold" and activation is not None),
                write_kind=write_kind,
                enriching_refresh=enriching_refresh,
            )
            # 8. Record the observation; log a report at the configured interval.
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

    async def _prefetch_daemon(
        self, chat_id: str, user_message: str,
        state: str, activation,
    ) -> tuple[list[dict], bool, str | None, dict]:
        """Run the prefetch mechanisms, branched on the turn ``state``.

        ``state`` (``cold`` / ``warm`` / ``drift``) comes from ``classify_turn``
        over the per-chat activation (set in ``run()``):

          * warm  — the thread is stable; serve the held activation's results
            (re-scored by recency via ``rescore_recency``), skipping every L3
            search. The brain trusts its recent activation.
          * drift — the query moved but not enough to be a shift; a targeted
            single-mechanism refresh re-runs the thematic search (uncached)
            against the new query — the only mechanism that can surface new
            associations on a moved-but-not-shifted thread.
          * cold  — first turn, embedding degraded, or a consensus shift: the full
            three-mechanism search (thematic cached + temporal + specific-key).

        Cold-path cache_hit/cache_key come from the thematic (cached) mechanism.
        Any single cold-path mechanism that raises is skipped
        (``return_exceptions=True``). Returns ``(results, cache_hit, cache_key)``.
        """
        layers = self._layers
        if state == "warm":
            # Hold the activation steady; attenuate recency only (no search). This
            # is the coherence payoff: the LLM builds on the same reality as the
            # previous turn instead of re-grounding from a jittering L3 slice.
            merged = rescore_recency(activation.merged, time.time()) if activation and activation.merged else []
            log.info("prefetch warm chat=%s served=%d", chat_id, len(merged))
            meta = {"warm_served": True, "thematic": len(merged), "temporal": 0, "specific_key": 0}
            return merged, False, None, meta

        after_before = extract_temporal_range(user_message)
        keys = extract_structural_keys(user_message)
        log.debug(
            "prefetch classify chat=%s state=%s temporal=%s specific_key=%s keys=%s",
            chat_id, state, after_before is not None, bool(keys), keys,
        )
        limit = PREFETCH_MAX_RESULTS

        if state == "drift":
            # Targeted refresh — re-run only thematic against the new query.
            fresh = enforce_result_limit(await layers.search_episodic(user_message, limit=limit))
            merged = merge_results([fresh])[:limit]
            log.info("prefetch drift chat=%s merged=%d", chat_id, len(merged))
            meta = {"warm_served": False, "thematic": len(fresh), "temporal": 0, "specific_key": 0}
            return merged, False, None, meta

        # cold: full three-mechanism search.
        async def _thematic() -> dict:
            results, hit, key = await layers.search_episodic_with_cache(
                chat_id, user_message, limit=limit,
            )
            return {"results": results, "cache_hit": hit, "cache_key": key}

        async def _temporal(rng: tuple[float, float]) -> dict:
            after, before = rng
            results = await layers.search_episodic(
                user_message, limit=limit, after=after, before=before,
            )
            return {"results": results, "cache_hit": False, "cache_key": None}

        async def _specific_key(matched_keys: list[str]) -> dict:
            results = await layers.find_by_keys(chat_id, matched_keys, limit=limit)
            return {"results": results, "cache_hit": False, "cache_key": None}

        # Each task is tagged with its mechanism name so a per-mechanism
        # exception (e.g. a ChromaDB HNSW/metadata desync — "Error finding
        # id") self-identifies in the log instead of appearing as an anonymous
        # "prefetch mechanism raised" with no source.
        tasks: list = [("thematic", _thematic())]
        if after_before is not None:
            tasks.append(("temporal", _temporal(after_before)))
        if keys:
            tasks.append(("specific_key", _specific_key(keys)))
        names = [name for name, _ in tasks]
        parts = await asyncio.gather(
            *[coro for _, coro in tasks], return_exceptions=True,
        )

        groups: list[list[dict]] = []
        cache_hit = False
        cache_key: str | None = None
        thematic_count = temporal_count = specific_key_count = 0
        for name, part in zip(names, parts):
            if isinstance(part, BaseException):
                log.warning(
                    "prefetch mechanism raised chat=%s mechanism=%s: %s",
                    chat_id, name, part,
                )
                continue
            count = len(part["results"])
            if name == "thematic":
                thematic_count = count
            elif name == "temporal":
                temporal_count = count
            elif name == "specific_key":
                specific_key_count = count
            groups.append(part["results"])
            if part.get("cache_key") is not None:
                cache_hit = part["cache_hit"]
                cache_key = part["cache_key"]

        merged = merge_results(groups)[:limit]
        log.info(
            "prefetch merge chat=%s state=cold mechanisms=%d merged=%d thematic=%d temporal=%d specific_key=%d",
            chat_id, len(parts), len(merged), thematic_count, temporal_count, specific_key_count,
        )
        ids = [
            r.get("metadata", {}).get("message_id")
            for r in merged
            if r.get("metadata", {}).get("message_id")
        ]
        if ids:
            asyncio.create_task(layers.bump_access(chat_id, ids))
        meta = {
            "warm_served": False,
            "thematic": thematic_count,
            "temporal": temporal_count,
            "specific_key": specific_key_count,
        }
        return merged, cache_hit, cache_key, meta

    async def _update_activation(
        self, layers, chat_id: str, intent: str, query_emb, turn_state: str,
        activation, l3_results: list[dict],
    ):
        """Persist or refresh the per-chat activation after a successful prefetch.

        Returns the activation now in effect (the post-turn thread), or ``None``
        when no activation could be built (no embedding). ``warm`` keeps the
        centroid steady and only extends the recent-queries window (a short
        follow-up must not move the thread); ``cold``/``drift`` build a fresh
        activation around the new query + results. The activation is the
        (centroid, merged, last_query, recent_queries) tuple the enriching-write
        refresh and the next turn's ``classify_turn`` use.
        """
        now = time.time()
        if turn_state == "warm":
            if activation is None:
                return None
            activation.recent_queries = trim_recent(activation.recent_queries, intent)
            activation.ts = now
            await layers.set_activation(chat_id, activation)
            return activation
        # cold or drift: a new / refreshed thread needs an embedding to anchor the
        # centroid; without it the activation can't classify future turns, so
        # degrade (leave absent → next turn cold).
        if query_emb is None:
            return None
        recent = trim_recent(activation.recent_queries if activation else [], intent)
        new_act = Activation(
            centroid=query_emb, merged=l3_results, last_query=intent,
            recent_queries=recent, ts=now,
        )
        await layers.set_activation(chat_id, new_act)
        return new_act

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
                activation.merged = merge_results([fresh])[:PREFETCH_MAX_RESULTS]
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
            loop_msgs = loop_msgs + tool_exchange
            # In-context grounding support, applied every iteration. Below the
            # cap the model is offered tools so it may chain another tool instead
            # of answering (decisional); at the cap tools are withheld so it must
            # synthesize from what it has.
            loop_msgs.append({"role": "user", "content": _TOOL_SYNTHESIS_BRIDGE})
            if iteration + 1 < AGENTIC_MAX_ITERATIONS:
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
                log.warning("tool_round cap-forced synthesis chat=%s iters=%d",
                            chat_id, iteration + 1)
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
    ) -> tuple[str, str, list[str]]:
        """Handle DSML-format tool calls (deepseek-v4-flash fallback path).

        Parses DSML markup from content, executes each tool, then makes one
        synthesis LLM call (when api_msgs is provided) so the user sees a
        natural reply rather than raw tool output.

        Previously this returned raw joined tool output with no synthesis call —
        the root cause of raw tool results appearing in chat. The fix mirrors
        _run_tool_round: execute tools, then call the LLM with
        _TOOL_SYNTHESIS_BRIDGE so it composes a response from the results.

        Returns (reply, tool_summary, stored_contents) to match _run_tool_round
        so call sites can update tracking uniformly.
        """
        invocations = _DSML_INVOKE.findall(content)
        if not invocations:
            log.warning("DSML block detected but no parseable invocations chat=%s", chat_id)
            return "", "", []
        calls_and_results: list[tuple[str, str, str]] = []
        stored_contents: list[str] = []
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
                    result = json.dumps({"error": str(exc)}
)
            calls_and_results.append((name, args_json, result))
            if name == "store_memory":
                stored_contents.append(self._extract_content(args_json))
        raw = "\n\n".join(r for _, _, r in calls_and_results)
        tool_summary = _compact_tool_summary(calls_and_results)
        if not api_msgs:
            # No conversation context available — return raw output as fallback.
            log.warning("DSML synthesis skipped (no api_msgs) chat=%s", chat_id)
            return raw, tool_summary, stored_contents
        # Synthesize: present the tool results to the model and ask it to respond
        # naturally. This mirrors the synthesis step in _run_tool_round and is
        # the fix for raw tool output reaching the user on the DSML path.
        synth_msgs = list(api_msgs) + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"Tool results:\n{raw}\n\n{_TOOL_SYNTHESIS_BRIDGE}"},
        ]
        try:
            r = await self._llm.chat.completions.create(
                model=settings.MODEL_NAME, messages=synth_msgs,
                temperature=settings.TEMPERATURE, max_tokens=settings.MAX_TOKENS,
            )
            reply = r.choices[0].message.content or raw
        except Exception as exc:
            log.warning("DSML synthesis LLM call failed chat=%s: %s — returning raw", chat_id, exc)
            reply = raw
        return reply, tool_summary, stored_contents

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