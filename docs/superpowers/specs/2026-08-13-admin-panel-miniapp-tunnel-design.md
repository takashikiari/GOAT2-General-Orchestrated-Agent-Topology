# Admin Panel — Telegram Mini App via Cloudflare Tunnel — Design

**Date:** 2026-08-13
**Status:** Approved (pending user spec review)
**Scope:** Expose the already-built, local-only admin panel (`admin_panel/`) as a Telegram Mini App, reachable through a Cloudflare Quick Tunnel whose URL changes on every bot restart, authenticated via Telegram's `initData` mechanism restricted to the configured admin.

---

## 1. Goal

The admin panel backend (metrics, logs, memory browsing, conversations) currently only answers on `127.0.0.1:8765` — reachable solely from the machine running the bot. This feature makes it reachable from the operator's phone/browser via Telegram itself: the bot's Telegram menu button opens the panel inside a Telegram Mini App webview, tunneled out through Cloudflare so no inbound firewall port ever needs to open.

This is explicitly **not** the full React frontend (that remains a separate, later plan). The deliverable here is the tunnel + auth + menu-button automation, proven end-to-end with a minimal placeholder page that fetches one real endpoint and displays it.

## 2. Constraints (from user decisions)

- **Quick Tunnel, not Named Tunnel.** No Cloudflare account, no DNS setup. `cloudflared tunnel --url ...` assigns a fresh random `*.trycloudflare.com` hostname on every run — this is the "auto-updating subdomain on every restart" behavior the user asked for, not something to build ourselves.
- **Access restricted to the configured admin only.** After validating the `initData` HMAC signature, the route also checks `user.id` from `initData` equals the existing `admin_chat_id` (`goat2.toml`). No other Telegram user, even with the URL, gets past `401`.
- **Menu button auto-updates on every bot startup**, via `bot.set_chat_menu_button(...)` — no manual step, no `/command` the operator has to remember to run after a restart.
- **Auth must be uniform on every `/api/*` route, no bypass for "local" requests.** Once `cloudflared` tunnels to `127.0.0.1:8765`, a real internet request arriving via the tunnel and a genuine local `curl` are indistinguishable at the socket level — there is no way to special-case one over the other, so this isn't a design choice, it's a consequence of the tunnel's mechanics.
- **Opt-in, defaults off.** `[tunnel] enabled = false` in `config/admin_panel.toml` by default — running a tunnel changes the panel's security posture (internet-reachable, even if authenticated) from the purely-local v1, so it must be an explicit choice, not silently enabled for everyone who pulls this code.
- **No new runtime secret.** Auth reuses the existing `TELEGRAM_BOT_TOKEN` (already in `.env`/`config/settings.py`) as the HMAC key — this is the standard Telegram WebApp auth mechanism, not a custom scheme.
- **File-size rule:** every new/modified file stays ≤ 260 lines, single responsibility.
- **The tunnel must never block or crash bot startup**, matching the existing `admin_panel.server.start` resilience pattern: guarded, lazy-imported, logs a WARNING and continues on any failure (missing `cloudflared` binary, timeout waiting for the assigned URL, `set_chat_menu_button` API failure).

## 3. Architecture

```
proces Python: telegram_interface.bot (run_polling)

  PTB Application ──┐
                     ├── același event loop asyncio ──┐
  FastAPI/uvicorn  ──┘                                 │
  (admin_panel, deja existent, 127.0.0.1:8765)          │
         ▲                                              │
         │ NOU: dependency FastAPI care validează        │
         │ initData pe toate rutele /api/*                │
         │                                                 ▼
  NOU: cloudflared subprocess ──── citește URL ──► set_chat_menu_button
  (tunnel --url http://127.0.0.1:8765)              (Telegram Bot API)
```

```
         │ conexiune outbound (fără port deschis pe firewall)
         ▼
   Cloudflare Edge ── https://xxxx.trycloudflare.com (nou la fiecare pornire)
         │
         ▼
   Telegram client (Mini App webview) → deschide URL-ul din butonul de meniu
```

**Key points:**

- `admin_panel/server.py` itself does not change its bind address — it stays on `127.0.0.1` exactly as before. `cloudflared` is what makes the local port reachable from outside, via an outbound connection to Cloudflare's edge; no inbound port is ever opened.
- The tunnel and the auth dependency are two independent, separately-guarded additions layered onto the existing panel — the panel continues to work exactly as it does today (local-only, no auth) when `[tunnel] enabled = false`.
- Both the panel server start and the tunnel start are launched from the same `post_init` hook (`telegram_interface/_plugin_scanner.py`), following the exact pattern already established for the admin panel server itself: lazy import, wrapped in `try/except`, logs and continues rather than propagating into bot startup.

## 4. Components

### `admin_panel/tunnel.py` (new)

- `async def start(application) -> None` — entry point called from `post_init`. No-ops immediately if `[tunnel] enabled = false` (config read via a new `admin_panel/tunnel_config.py`, following the existing `*_config.py` + `config/*.toml` loader pattern).
- Launches `cloudflared tunnel --url http://{ADMIN_HOST}:{ADMIN_PORT}` via `asyncio.create_subprocess_exec`, reads `stderr` line by line.
- `extract_tunnel_url(line: str) -> str | None` — pure function, `TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")`, unit-testable against sample `cloudflared` log lines without a real subprocess.
- On finding the URL (within a bounded timeout, `[tunnel] timeout_seconds = 15` in `config/admin_panel.toml`): calls `await application.bot.set_chat_menu_button(chat_id=admin_chat_id, menu_button=MenuButtonWebApp(text="Admin Panel", web_app=WebAppInfo(url=url)))`.
- `admin_chat_id` comes from the new shared `config/admin_chat.py::load_admin_chat_id()` (see below) — not from `telegram_interface.bot`, to avoid a circular import (`bot.py` → `_plugin_scanner.py` → `bot.py`).
- All failure modes (binary missing → `FileNotFoundError`; timeout; `set_chat_menu_button` raising) are caught, logged at WARNING, and never propagate — the tunnel is best-effort.

### Small refactor: `config/admin_chat.py` (new)

`telegram_interface/bot.py` currently has a private `_load_admin_chat_id()` reading `goat2.toml`. `tunnel.py` needs the same value but can't import it from `bot.py` without creating a cycle. Extract the logic into `config/admin_chat.py::load_admin_chat_id() -> str` (identical behavior — read `goat2.toml`, `interface.telegram.admin_chat_id`, default `""`). `bot.py` is updated to import and use this shared function, removing its private duplicate — this eliminates the duplication rather than just working around it.

### `admin_panel/telegram_auth.py` (new)

- `def verify_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None` — pure function. Reconstructs Telegram's `data_check_string` from the parsed `init_data` query string (all fields except `hash`, sorted, joined by `\n`), computes `secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)`, then `computed_hash = HMAC_SHA256(key=secret_key, msg=data_check_string).hexdigest()`, compares to the `hash` field (constant-time comparison via `hmac.compare_digest`). Also checks `auth_date` is within `max_age_seconds` of now. Returns the parsed dict (including `user`) on success, `None` on any failure (bad signature, expired, malformed input) — never raises on untrusted input.
- `require_admin_auth` — a FastAPI dependency (`Depends`) that reads the `X-Telegram-Init-Data` request header, calls `verify_init_data`, checks the returned `user["id"]` against `load_admin_chat_id()`, and raises `HTTPException(401)` if any step fails (missing header, bad signature, expired, wrong user).
- Applied explicitly per-router in `create_app` (`admin_panel/server.py`): `app.include_router(metrics.router, dependencies=[Depends(require_admin_auth)])`, same for `logs`, `memory`, `conversations`. The static placeholder page route (`GET /`) is NOT behind this dependency — it carries no sensitive data, and gating it would only complicate the page's own ability to load before it can present an error.
- New config: `[tunnel] enabled = false`, `[tunnel] timeout_seconds = 15` (how long to wait for the URL in `cloudflared`'s output before giving up), and `[auth] max_age_seconds = 86400` — new sections in the existing `config/admin_panel.toml`, loaded by `admin_panel/tunnel_config.py` and `admin_panel/auth_config.py` respectively (small, single-purpose loaders, matching the codebase's established one-file-per-concern config pattern).

### `admin_panel/static/index.html` (new — placeholder, not the final frontend)

A single static file: loads `https://telegram.org/js/telegram-web-app.js`, calls `Telegram.WebApp.ready()`, reads `Telegram.WebApp.initData`, `fetch`es `/api/metrics` with header `X-Telegram-Init-Data: <initData>`, and renders the raw JSON (or the error) on the page. Served via a new `GET /` route in `admin_panel/server.py` (`FileResponse` or an inlined string response — implementation detail for the plan). This is the acceptance proof for the whole feature: tunnel → Telegram webview → auth → real backend data, end to end.

## 5. Data flow

### Startup (extends the existing `post_init` sequence)

1. `admin_panel.server.start(registry)` starts as today.
2. **New:** `admin_panel.tunnel.start(application)` starts immediately after, as its own guarded task (lazy import + `try/except` inside `_post_init`, mirroring the admin-server wiring already established).
3. If `[tunnel] enabled = false` (the shipped default): `tunnel.start` returns immediately, nothing else about the bot's startup changes.
4. If `enabled = true`: `cloudflared` launches, the URL is parsed from its stderr within the timeout, and the chat menu button is updated via the Telegram Bot API.

### Request lifecycle through the Mini App

1. The admin opens their Telegram chat with the bot and sees the menu button (already pointing at the current tunnel URL).
2. Tapping it opens a Telegram webview loading `https://xxxx.trycloudflare.com/` → Cloudflare Edge → the local `cloudflared` process → `127.0.0.1:8765` → FastAPI serves `GET /` (the placeholder page).
3. The page's JS calls `Telegram.WebApp.ready()`, reads `initData`, and fetches `/api/metrics` with `X-Telegram-Init-Data` set.
4. The FastAPI dependency validates signature + freshness + `user.id` — allows or responds `401`.
5. The page renders the JSON response or the error.

## 6. Error handling

| Failure | Effect |
|---|---|
| `cloudflared` binary missing | WARNING logged, tunnel disabled, panel stays local-only — identical to today's behavior |
| URL doesn't appear in `stderr` within `timeout_seconds` (default 15) | WARNING, menu-button update skipped, bot continues normally |
| `set_chat_menu_button` fails (Telegram API unreachable) | WARNING, tunnel stays up — only the visible button in the chat fails to update |
| `cloudflared` dies mid-session | The read loop hits EOF, WARNING logged; **no auto-reconnect in v1** (YAGNI — a flaky tunnel is fixed by restarting the bot, not worth a reconnection loop yet) |
| `initData` missing or bad signature | `401` on every `/api/*` request |
| `initData` valid but `user.id` ≠ `admin_chat_id` | `401` — blocks anyone else who obtains the URL |
| `initData` expired (`auth_date` too old) | `401` — replay protection |
| Bot restarts while the Mini App is already open in a client | The open webview keeps pointing at the now-dead URL until manually reopened — known, accepted limitation, not addressed by this design |

## 7. Testing

- `verify_init_data` — pure-function tests: construct valid/tampered/expired `init_data` strings by hand using a test bot token and the real algorithm, assert accept/reject in each case. No FastAPI, no network.
- `extract_tunnel_url` — pure-function tests against literal sample lines copied from real `cloudflared` output.
- `require_admin_auth` — `TestClient`-based tests with valid/missing/invalid/wrong-user headers on a minimal app, matching the existing per-route test convention in `admin_panel/routes/*`.
- The subprocess launch itself and the live `set_chat_menu_button` call are not practically unit-testable (external process, real Telegram API) — verified manually, the same way Task 10 of the admin-panel-backend plan was: a standalone script exercising the real `cloudflared` binary and inspecting its output, without touching the live production bot process.

## 8. Out of scope (this iteration)

- The full React frontend (separate, later plan) — this feature only needs the placeholder page to prove the pipeline.
- Auto-reconnect if `cloudflared` crashes mid-session.
- Named Tunnel / stable custom domain support.
- Any write/mutating capability through the Mini App — the panel remains read-only, unchanged from the existing admin-panel-backend design.
- Notifying the operator in-chat when the tunnel comes up (e.g. a "panel is ready" message) — the menu button itself is the notification.

**How to apply:** Follow the DI and guarded-optional-feature patterns already established by `admin_panel/server.py` and its `post_init` wiring — every new failure mode here must degrade to a logged WARNING, never an exception that reaches bot startup. New config stays in `config/admin_panel.toml` via small per-concern loader modules, matching the existing convention. See the base admin panel design at [[admin-panel-design]] (`docs/superpowers/specs/2026-08-13-admin-panel-design.md`) for the panel this builds on.
