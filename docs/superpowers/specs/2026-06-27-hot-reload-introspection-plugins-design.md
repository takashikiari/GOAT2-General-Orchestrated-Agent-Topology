# Hot-Reload Introspection Plugins — Design

**Date:** 2026-06-27
**Status:** Approved (pending user spec review)
**Scope:** Give GOAT-on-Telegram on-demand visibility into its own live logs and memory metrics, via a hot-reload plugin system for non-core orchestrator tools.

---

## 1. Goal

When a user chats with GOAT over Telegram and asks about its own state — "how's your memory doing?", "show me recent logs", "what's your cache hit rate?" — GOAT must be able to inspect its live logs and memory metrics and answer truthfully, rather than saying "I don't recall" or guessing.

This is delivered **on-demand only**: the model calls tools when asked. There is **no always-on context injection** — no per-turn token cost when the user never asks.

## 2. Constraints

- **Core tools stay hardcoded.** `search_memory` and `store_memory` remain wired in `telegram_interface/bot.py:build_app`. They are tightly bound to `registry.memory_layers` (core infra), always present, and not made pluggable now. The plugin system is designed so they *could* later become plugins ("hybrid"), but that is out of scope (YAGNI).
- **All other tools are hot-reload plugins** under `tools/goat_skills/`.
- **No new runtime dependencies.** No `watchdog`; hot reload is a 30-second polling scan (mtime-based), stdlib only.
- **File-size rule:** every new file ≤ 90 lines, single responsibility, split before writing.
- **Zero-singleton rule:** the plugin manager is registry-owned (lazy property on `ServiceRegistry`), not a module-level singleton.
- **Concurrency:** the Telegram bot serves many chats at once. A reload must atomically swap the live tool list — never mutate it in place while a turn is mid-flight.

## 3. Architecture

```
Telegram message
  → Orchestrator.run()
      core tools (hardcoded):  search_memory, store_memory
      plugin tools (live):      registry.plugin_manager.tools   ← re-read every turn
      → LLM picks a tool → _call_tool dispatches across the combined list

Background: a 30s asyncio task calls registry.plugin_manager.scan()
            which reconciles tools/goat_skills/*.py (import / reload / drop).
```

The orchestrator never caches the plugin tool list — it reads `registry.plugin_manager.tools` fresh each turn, so a reload is visible to the very next message without rebuilding anything.

## 4. Plugin contract (`tools/goat_skills/*.py`)

One file per concern. Each plugin module exposes a single entry point and receives the registry for dependency injection (matches the rest of the codebase):

```python
def build(registry: ServiceRegistry) -> list[ToolDefinition]: ...
```

- Returns 1+ `ToolDefinition` objects (reuses the existing `orchestrator.tools.ToolDefinition` — no new type introduced).
- A file that throws on import, has no `build`, or returns the wrong type is **skipped and logged**. It never crashes the bot or removes a previously-good tool.
- Package init `tools/goat_skills/__init__.py` makes the directory importable as `tools.goat_skills`.
- Initial plugins: `get_memory_metrics.py`, `get_recent_logs.py`.

## 5. `PluginManager` (registry-owned)

New module `plugins/plugin_manager.py`. Registry-owned via a `plugin_manager` lazy property on `ServiceRegistry` (mirrors `memory_analytics`).

State:
- `_registry` — passed to each plugin's `build`.
- `_dir: Path` — the `tools/goat_skills/` directory.
- `_mtimes: dict[str, float]` — last-seen mtime per plugin file.
- `_modules: dict[str, module]` — cached imported modules.
- `_tools: list[ToolDefinition]` — the current live tool list (immutable per reconcile; swapped wholesale).

Methods:
- **`scan()`** — full reconcile against `tools/goat_skills/*.py`:
  1. `os.stat` each `*.py` (excluding `__init__.py`), compare mtime to `_mtimes`.
  2. **New file** → import the module, store in `_modules`.
  3. **Changed mtime** → `importlib.reload(module)`.
  4. **Removed file** → drop from `_modules`.
  5. Rebuild the tool list by calling each surviving module's `build(registry)`, guarding each call.
  6. **Failure isolation:** if a plugin errors on import, reload, or `build`, keep its last-known-good tools (only first-seen failures leave a plugin absent). Log the exception at WARNING.
  7. **Atomically swap** `self._tools` to a brand-new list — never mutate in place.
- **`tools`** property — returns the current list (callers take a snapshot reference).

Approx. 60 lines, single responsibility.

## 6. Orchestrator change (small)

Add one helper and use it in the schema/dispatch paths:

```python
def _all_tools(self) -> list[ToolDefinition]:
    return [*self._tools, *self._registry.plugin_manager.tools]

def _has_tool(self, name: str) -> bool:
    return any(t.name == name for t in self._all_tools())
```

- `run()`: build `kw["tools"]` and the tool-round dispatch from `_all_tools()` instead of `self._tools`.
- `_has_search_memory` / `_has_store_memory` stay on `self._tools` (they are core, always present).
- `_has_tool(name)` drives the introspection guidance check.

Net: ~6 lines added; no behavior change to the existing core-tool flow.

## 7. Hot-reload wiring (30s polling, no new dependency)

A plain `asyncio` background task — not `watchdog`, not the `job-queue` extra:

- `run_polling()` starts the bot with a `post_init` hook that:
  1. runs one initial `registry.plugin_manager.scan()` (plugins live before the first message), then
  2. `asyncio.create_task`s a loop: `while True: await asyncio.sleep(30); registry.plugin_manager.scan()`.
- The task is created inside `post_init` (async context) and dies with the application. Exceptions inside the loop are caught and logged so a single failed scan never kills the watcher.

Approx. 10 lines in `bot.py` (a small `_start_plugin_scanner` helper).

## 8. The two introspection plugins (on-demand only)

### `tools/goat_skills/get_memory_metrics.py`
- Calls `registry.memory_analytics.get_report()`, returns it as indented JSON.
- Parameters: `{}` (no arguments).
- Description (model-facing): *"Live aggregated memory metrics: cache hit rate, prefetch attempt/success/timeout rates, tier hit rates, top intents, average latency per stage (classify/search/assemble/inject), and average tokens injected per tier. Call when asked how your memory is doing."*

### `tools/goat_skills/get_recent_logs.py`
- Reads the last `minutes` minutes of the application log file the logger actually writes.
- **Sources the log path from the logging module** (`utils/logging/setup.py` exports `LOG_FILE`), so writer and reader share one source of truth — this also fixes the pre-existing path mismatch (writer → `/tmp/goat2/logs/goat2.log`, old MCP reader → `repo/logs/goat2.log`).
- Parameters: `minutes` (default 30), `level` (default "ALL"; one of ALL/DEBUG/INFO/WARNING/ERROR/CRITICAL, case-insensitive).
- Output capped at a max line count; lines returned most-recent-last.
- Description (model-facing): *"Recent lines from GOAT's own log file, optionally filtered by level. Call when asked about recent logs, warnings, or errors."*
- Missing/unreadable log file → returns a human-readable message, not an exception.

### System-prompt guidance (appended only when the tools are present)
Mirrors the existing `_SEARCH_MEMORY_GUIDANCE` / `_STORE_MEMORY_GUIDANCE` pattern:

> *"If the user asks about your own logs, memory metrics, cache hit rate, latency, or how your memory is doing, use `get_memory_metrics` and `get_recent_logs` to inspect your live state before answering."*

Appended in `run()` when `_has_tool("get_memory_metrics")` or `_has_tool("get_recent_logs")`.

## 9. Error handling

- Plugin import / reload / `build` failure → `log.warning`, skip that plugin, keep its prior good tools (no wipe-on-broken-edit).
- Background scan task exception → log + continue; the watcher never dies from a single bad cycle.
- Tool handler exception → already handled in `_call_tool` (returns `{"error": ...}`).
- Log file missing/unreadable → `get_recent_logs` returns a human-readable message, not an exception.

## 10. Testing

- **PluginManager unit tests** (temp `goat_skills/` dir):
  - add file → tool appears;
  - touch (mtime change) → reloads;
  - delete → drops;
  - broken file (raises on import) → skipped, others unaffected;
  - `build` returning wrong type → skipped, others unaffected;
  - reload of a now-broken plugin → previous good tools retained.
- **Contract test:** a sample plugin's `build(registry)` returns valid `ToolDefinition`s with correct schema.
- **Orchestrator integration test:** a plugin tool appears in the LLM tool schema, is callable, and a mid-run `scan()` adding a plugin makes it visible on the next turn.
- **Plugin unit tests:**
  - `get_memory_metrics` returns the report JSON (seed `MemoryAnalytics`, assert keys).
  - `get_recent_logs` returns windowed lines and handles a missing file gracefully.
- **Concurrency:** the immutable-swap design guarantees a turn in flight sees a consistent list; verified by inspection of the atomic-replace path.

## 11. File inventory and sizes (90-line rule)

| File | Action | Est. lines |
|---|---|---|
| `plugins/plugin_manager.py` | new | ~60 |
| `tools/goat_skills/__init__.py` | new | ~5 |
| `tools/goat_skills/get_memory_metrics.py` | new | ~35 |
| `tools/goat_skills/get_recent_logs.py` | new | ~50 |
| `registry/registry.py` | +`plugin_manager` property | +~12 |
| `orchestrator/orchestrator.py` | +`_all_tools`/`_has_tool`, guidance | +~10 |
| `utils/logging/setup.py` | export `LOG_FILE` | +~2 |
| `telegram_interface/bot.py` | `_start_plugin_scanner` + post_init | +~15 |

No file crosses 90 lines. The orchestrator grows by ~10 lines only (no split needed).

## 12. Out of scope (YAGNI)

- Making `search_memory` / `store_memory` into plugins ("hybrid" core tools).
- An in-memory ring buffer of recent `MemoryObservation` objects as an alternative log source (the real log file is used instead).
- A `/reload-tools` Telegram command (hot reload is automatic via the 30s scan).
- Per-recent-turn introspection beyond the aggregate `get_report()` (no ring buffer of individual observations).
- Wiring the `mcp_server` tools (that subsystem targets a different architecture and is intentionally ignored).