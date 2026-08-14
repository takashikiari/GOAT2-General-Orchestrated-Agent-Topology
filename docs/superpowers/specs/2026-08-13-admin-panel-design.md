# Admin Panel — Design

**Date:** 2026-08-13
**Status:** Approved (pending user spec review)
**Scope:** A local, read-only web admin panel for GOAT 2.0 exposing memory metrics, live logs, L1/L2/L3 memory browsing, and per-chat conversation timelines.

---

## 1. Goal

GOAT 2.0 today has zero HTTP surface — the only interface is the Telegram bot, and the only way to inspect its own state is by asking GOAT to call `get_memory_metrics`/`get_recent_logs` in chat. This is fine for GOAT to self-report, but there is no way for the operator (the user running the bot) to visually browse memory tiers, tail logs, or inspect a conversation's history without going through chat or raw Python/redis-cli/Chroma queries.

This design adds a small, **read-only**, **local-only** admin panel: a FastAPI backend embedded in the existing bot process, and a React+Vite frontend, giving the operator a dashboard for:

1. Memory metrics (cache hit rate, prefetch rates, tier hit rates, latency per stage)
2. Live logs (tail, filterable by level/time window)
3. Memory browsing (L1 facts, L2 working memory, L3 episodic)
4. Conversations (list chat_ids, view a unified per-chat timeline)

## 2. Constraints (from user decisions)

- **Read-only, v1.** No delete/promote/clear actions from the panel. (User's explicit choice — actions are a possible future iteration.)
- **Local only.** Binds to `127.0.0.1`, no authentication. Not designed for LAN or internet exposure.
- **Same process as the Telegram bot.** Metrics live only in the bot process's in-memory `MemoryAnalytics` (persisted only as JSON log lines, not queryable elsewhere) — the user chose exact/live metrics over a standalone process that would have to approximate them by parsing logs.
- **Panel requires the bot running.** This is the direct consequence of the above: if `telegram_interface.bot` isn't running, the panel isn't reachable.
- **React + Vite frontend** — user's explicit choice over a no-build-step plain HTML/JS page.
- **File-size rule:** every new file ≤ 260 lines, single responsibility (see [[feedback-file-size-rule]]).
- **No new logic for data access.** The panel calls existing public methods on `registry.memory_layers` / the physical tiers / `registry.memory_analytics`; it does not reimplement retrieval, budget, or analytics logic.

## 3. Architecture

```
proces Python: telegram_interface.bot (run_polling)

  PTB Application (polling loop)  ──┐
                                     ├── same asyncio event loop ──┐
  FastAPI app (admin_panel/*)     ──┘                              │
  started as asyncio.create_task                                   │
  in post_init hook, via                                           │
  uvicorn.Server(...).serve()                                      │
                                                                     ▼
                                                          registry (ServiceRegistry,
                                                          one shared instance)
                                                                     │
                          ┌──────────────────┬───────────────────────┬───────────────┐
                          ▼                  ▼                       ▼               ▼
                    Redis (L2)         ChromaDB (L3)             Letta (L1)       log file
                    working memory     episodic                  facts            (get_recent_logs)

                                                          ▲
                                                          │ HTTP (127.0.0.1 only, read-only JSON)
                                                          │
                                              React + Vite frontend
                                              (npm run build → dist/,
                                               served by FastAPI as static files)
```

**Key points:**

- One process, one `registry` instance. The FastAPI server is started the same way the existing plugin scanner is started today — as a background asyncio task from `post_init_hook` (`telegram_interface/_plugin_scanner.py` already establishes this idiom; the admin server follows it, not a new pattern).
- The panel introduces no new data-access logic — only thin HTTP routes over methods that already exist: `permanent.get_all_facts()`, `working.list_chat_ids()`/`get_messages()`, `episodic.get_recent()`/`get_oldest()`, `registry.memory_analytics.get_report()`, and the tail logic in `tools/goat_skills/get_recent_logs.py` (extracted to a shared helper so both the LLM tool and the API route use one implementation).
- The panel must never block or crash the Telegram bot. If the admin server fails to start (e.g. port in use) or a route errors, the bot's polling loop is unaffected.
- In production/day-to-day use, the frontend is a static build served by the same FastAPI process (one port, no CORS). During active frontend development, `vite dev` can run separately with a proxy to the API (CORS allowed for localhost only in dev).

## 4. Components

### 4.1 Backend — new `admin_panel/` package

- **`admin_panel/server.py`** — builds the `FastAPI()` app, mounts the routers below, mounts `dist/` (the React build output) as static files. Exposes `async def start(registry)` that builds a `uvicorn.Config`/`Server` bound to `127.0.0.1` and awaits `server.serve()`.
- **`admin_panel/admin_config.py`** + **`config/admin_panel.toml`** — `[server] host = "127.0.0.1"`, `port = 8765` (new port, chosen clear of the Redis/Letta/Telegram ports already in use; overridable in the toml). Follows the exact loader pattern already used by `telegram_interface/telegram_config.py` (defaults dict + `tomllib.load`, `FileNotFoundError` → defaults).
- **`admin_panel/routes/metrics.py`** — `GET /api/metrics` → `registry.memory_analytics.get_report()`.
- **`admin_panel/routes/logs.py`** — `GET /api/logs?minutes=30&level=ALL` → shared tail helper (extracted from `tools/goat_skills/get_recent_logs.py`).
- **`admin_panel/routes/memory.py`**:
  - `GET /api/memory/facts` — L1, `permanent.get_all_facts()`
  - `GET /api/memory/working/{chat_id}` — L2, `working.get_messages(chat_id)`
  - `GET /api/memory/episodic/{chat_id}?limit=50&order=recent|oldest` — L3, `episodic.get_recent`/`get_oldest`
- **`admin_panel/routes/conversations.py`**:
  - `GET /api/conversations` — union of `working.list_chat_ids()` (active sessions) and a new `EpisodicQueries.list_chat_ids()` (archived-only chats, past the L2 TTL); each entry tagged `active` or `archived_only`.
  - `GET /api/conversations/{chat_id}` — merged L2 (full evidence) + L3 (clean synthesized reply) timeline, sorted by timestamp, each entry tagged with its source tier.

**New method required:** `memory/episodic/queries.py::EpisodicQueries.list_chat_ids()` — reads only `metadatas` (not `documents`, keeping it cheap) from the collection and returns the deduped set of `chat_id` values. This is new code, needed because no existing method enumerates distinct chat_ids in ChromaDB; without it, "Conversations" would silently miss any chat whose L2 session already expired (`WORKING_TTL_SECONDS`).

Every route catches its backend's specific exceptions (`redis.RedisError`, `httpx.HTTPError` for Letta, ChromaDB exceptions) and returns `{"error": "<short message>"}` with HTTP 200 rather than a raw 500 — a single tier being down must degrade only its own panel section, not the whole page.

### 4.2 Frontend — React + Vite, 4 pages

Simple tab-based navigation (no router — unjustified for 4 static pages), plain `fetch()` + `useState`/`useEffect` (no react-query/redux — unjustified for a read-only 4-page panel):

1. **Dashboard** — metrics snapshot from `/api/metrics`; polling refresh every 5-10s.
2. **Logs** — level + time-window filters over `/api/logs`; manual or polling refresh.
3. **Memory** — tabs for L1 (facts) / L2 (pick chat_id → messages) / L3 (pick chat_id → episodic entries, recent/oldest toggle).
4. **Conversations** — chat_id list (active/archived_only badge) → click opens the unified L2+L3 timeline for that chat.

## 5. Data flow

### Startup

1. `run_polling()` builds `registry` and calls `Application.builder().post_init(post_init_hook(registry))`.
2. `post_init_hook` is extended (it already runs `plugin_manager.scan()` + a 30s loop) to also `asyncio.create_task(admin_panel.server.start(registry))`.
3. If the admin server fails to start (e.g. port already bound from a previous unclean shutdown), the exception is caught, logged at WARNING, and the bot continues normally without the panel. The panel is a debug convenience; the bot's Telegram function is the critical path and must never be put at risk by it.

### Request lifecycle

Browser → `fetch('/api/...')` → FastAPI route → existing async method on `registry.memory_layers` / a physical tier / `registry.memory_analytics` → JSON response.

All routes run on the same event loop as the bot. Since the underlying calls are already async or wrapped in `asyncio.to_thread` (e.g. ChromaDB calls in `episodic.py`), a slow admin request does not block Telegram message processing — the event loop interleaves both.

## 6. Error handling

Per-section degradation, not a blank page:

| Failure | Effect |
|---|---|
| Redis down | `/api/memory/working/*` and the L2 half of `/api/conversations/*` return `{"error": ...}`; L1/L3/logs/metrics stay functional. |
| ChromaDB unavailable | L3 routes and `list_chat_ids` (episodic half) return `{"error": ...}`. |
| Letta down | `/api/memory/facts` returns `{"error": ...}`. |
| Unknown `chat_id` | Not an error — empty list `[]`, matching existing `working.get_messages`/`episodic.get_recent` behavior. |
| Log file missing/rotated | Inherits existing behavior from `get_recent_logs.py` unchanged. |
| Admin port already in use at startup | Logged WARNING, bot continues without the panel. |

The frontend checks for an `error` key in each section's response and shows an inline banner scoped to that section only.

## 7. Testing

Routes are thin (delegate to already-tested methods on `memory_layers`/tiers), so tests are light integration tests: `TestClient(app)` against a `registry` built over local test services (Redis/Chroma), following the existing `conftest.py`/`benchmark/` fixture patterns. Cover: happy-path shape of each endpoint, and the degraded-backend case (a tier's client raising → route returns `{"error": ...}` with HTTP 200, not a crash). No automated frontend testing for v1 — manual verification in a browser is sufficient for an internal debug tool.

## 8. Out of scope (v1)

- Any write/mutating action (delete entry, promote fact, clear cache) — explicitly deferred by user choice.
- Authentication, LAN/internet exposure, HTTPS.
- Running the panel independently of the bot process.
- Automated frontend tests, WebSocket/streaming updates (polling is sufficient at this scale).

**How to apply:** Follow the DI pattern already established in the codebase — the admin server receives `registry`, never constructs its own. All new backend files stay within the file-size rule; a route file handling more than one concern should be split before it grows past the limit.
