# Admin Panel Mini App Tunnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing admin panel as a Telegram Mini App via a Cloudflare Quick Tunnel (fresh `*.trycloudflare.com` URL every restart), gated by Telegram `initData` auth restricted to the configured admin, with the chat menu button auto-updated on every bot startup.

**Architecture:** Two independent, separately-guarded additions layered onto the existing admin panel (`admin_panel/`, already local-only on `127.0.0.1:8765`): (1) an `initData`-verifying FastAPI dependency applied to every `/api/*` route, and (2) a `cloudflared` subprocess launcher that parses the assigned tunnel URL and updates the bot's chat menu button via the Telegram Bot API. Both start from the same `post_init` hook that already starts the admin server, both opt-in/guarded so a failure never blocks bot startup.

**Tech Stack:** `cloudflared` binary (already installed, invoked as a subprocess — no new Python dependency), stdlib `hmac`/`hashlib`/`json` for Telegram's official initData verification algorithm, `python-telegram-bot`'s `MenuButtonWebApp`/`WebAppInfo`/`Bot.set_chat_menu_button` (already a core dependency).

## Global Constraints

- File-size rule: every new/modified file stays ≤ 260 lines, single responsibility.
- Quick Tunnel only (no Cloudflare account, no DNS) — `cloudflared tunnel --url ...` assigns the rotating subdomain itself.
- Access restricted to `admin_chat_id` (from `goat2.toml`) only — verified `initData` signature is necessary but not sufficient; `user.id` must also match.
- Auth enforced uniformly on every `/api/*` route, no bypass for "local" requests — once `cloudflared` tunnels to `127.0.0.1`, a real internet request and a genuine local request are indistinguishable at the socket level.
- `[tunnel] enabled = false` by default in `config/admin_panel.toml` — opt-in, not automatically on.
- No new runtime secret — auth reuses the existing `settings.TELEGRAM_BOT_TOKEN`.
- Every new failure mode (missing `cloudflared`, timeout, Telegram API failure) degrades to a logged WARNING and must never block or crash bot startup — matching the exact guarded-lazy-import pattern already established for `admin_panel.server.start` in `telegram_interface/_plugin_scanner.py`.
- Follow the existing `*_config.py` + `config/*.toml` loader pattern exactly for all new config.
- Tests use local fakes/doubles (no live Redis/ChromaDB/Letta/Telegram API required to run the suite).

---

### Task 1: Extract `admin_chat_id` loading into a shared module

**Files:**
- Create: `config/admin_chat.py`
- Modify: `telegram_interface/bot.py`
- Test: `tests/test_admin_chat.py`

**Interfaces:**
- Produces: `config.admin_chat.load_admin_chat_id() -> str` — consumed by Task 4 (`admin_panel/telegram_auth.py`) and Task 6 (`admin_panel/tunnel.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_admin_chat.py`:

```python
"""tests.test_admin_chat — config.admin_chat.load_admin_chat_id."""
from __future__ import annotations

from config.admin_chat import load_admin_chat_id


def test_returns_empty_string_when_goat2_toml_missing(tmp_path, monkeypatch):
    import config.admin_chat as mod

    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == ""


def test_reads_admin_chat_id_from_goat2_toml(tmp_path, monkeypatch):
    import config.admin_chat as mod

    (tmp_path / "goat2.toml").write_text(
        '[interface.telegram]\nadmin_chat_id = "777"\n'
    )
    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == "777"


def test_returns_empty_string_on_malformed_toml(tmp_path, monkeypatch):
    import config.admin_chat as mod

    (tmp_path / "goat2.toml").write_text("not valid toml {{{")
    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    assert mod.load_admin_chat_id() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_admin_chat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.admin_chat'`

- [ ] **Step 3: Write `config/admin_chat.py`**

```python
"""config.admin_chat — shared admin_chat_id loader (goat2.toml).

Extracted from telegram_interface.bot so admin_panel modules can read the
same value: admin_panel.telegram_auth and admin_panel.tunnel cannot import
from telegram_interface.bot without creating a cycle
(bot.py -> _plugin_scanner.py -> admin_panel.* -> bot.py).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent

__all__ = ["load_admin_chat_id"]


def load_admin_chat_id() -> str:
    """Read admin_chat_id from goat2.toml, or empty string if not configured."""
    cfg = _ROOT / "goat2.toml"
    if not cfg.exists():
        return ""
    try:
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return str(data.get("interface", {}).get("telegram", {}).get("admin_chat_id", ""))
    except Exception:
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_chat.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Update `telegram_interface/bot.py` to use the shared loader**

In `telegram_interface/bot.py`:

1. Remove the `_load_admin_chat_id` function definition (currently lines 56-66 — the whole `def _load_admin_chat_id() -> str: ...` block).
2. Add this import right after the existing `from config import settings` line:
   ```python
   from config.admin_chat import load_admin_chat_id
   ```
3. Change the line `admin_chat_id = _load_admin_chat_id()` to:
   ```python
   admin_chat_id = load_admin_chat_id()
   ```

Do not remove the top-level `import tomllib` — it's still used elsewhere in `bot.py` (the update-checker reads `goat2.toml` too).

- [ ] **Step 6: Run the full test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 7: Commit**

```bash
git add config/admin_chat.py telegram_interface/bot.py tests/test_admin_chat.py
git commit -m "refactor(bot): extract admin_chat_id loading into config.admin_chat, shared with admin_panel"
```

---

### Task 2: `[auth]` config section

**Files:**
- Create: `admin_panel/auth_config.py`
- Modify: `config/admin_panel.toml`
- Test: `tests/test_auth_config.py`

**Interfaces:**
- Produces: `admin_panel.auth_config.AUTH_MAX_AGE_SECONDS: int` — consumed by Task 4 (`admin_panel/telegram_auth.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_auth_config.py`:

```python
"""tests.test_auth_config — admin_panel.auth_config constants + toml override."""
from __future__ import annotations

from admin_panel.auth_config import AUTH_MAX_AGE_SECONDS


def test_default():
    assert AUTH_MAX_AGE_SECONDS == 86400


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.auth_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text("[auth]\nmax_age_seconds = 60\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("auth", {})
    assert cfg["max_age_seconds"] == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_auth_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.auth_config'`

- [ ] **Step 3: Add `[auth]` to `config/admin_panel.toml`**

Append to the existing file (which currently has only `[server]`):

```toml

[auth]
# Max age (seconds) of a Telegram initData payload's auth_date before it's
# rejected as expired — replay protection for the Mini App auth (see
# docs/superpowers/specs/2026-08-13-admin-panel-miniapp-tunnel-design.md).
max_age_seconds = 86400
```

- [ ] **Step 4: Write `admin_panel/auth_config.py`**

```python
"""admin_panel.auth_config — Telegram initData auth config. Reads config/admin_panel.toml ([auth] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "auth": {
        "max_age_seconds": 86400,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("auth", _DEFAULTS["auth"])

AUTH_MAX_AGE_SECONDS: Final[int] = int(_cfg.get("max_age_seconds", _DEFAULTS["auth"]["max_age_seconds"]))

__all__ = ["AUTH_MAX_AGE_SECONDS"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_auth_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add admin_panel/auth_config.py config/admin_panel.toml tests/test_auth_config.py
git commit -m "feat(admin-panel): add [auth] config section for initData max_age_seconds"
```

---

### Task 3: `verify_init_data` — Telegram initData signature verification

**Files:**
- Create: `admin_panel/telegram_auth.py`
- Test: `tests/test_telegram_auth_verify.py`

**Interfaces:**
- Produces: `admin_panel.telegram_auth.verify_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None` — consumed by Task 4 (same file, `require_admin_auth`).

- [ ] **Step 1: Write the failing tests**

`tests/test_telegram_auth_verify.py`:

```python
"""tests.test_telegram_auth_verify — admin_panel.telegram_auth.verify_init_data.

Constructs signed initData strings by hand, using the same HMAC-SHA256
algorithm Telegram's real clients use, to prove verify_init_data accepts
exactly what a real Telegram Mini App would send and rejects tampering.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from admin_panel.telegram_auth import verify_init_data

_BOT_TOKEN = "123456:test-bot-token"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _build_init_data(user_id: int = 42, auth_date: int | None = None, bot_token: str = _BOT_TOKEN) -> str:
    fields = {
        "query_id": "AAEXample",
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def test_valid_init_data_returns_parsed_user():
    result = verify_init_data(_build_init_data(user_id=42), _BOT_TOKEN, max_age_seconds=86400)
    assert result is not None
    assert result["user"]["id"] == 42


def test_tampered_payload_rejected():
    fields = {
        "query_id": "AAEXample",
        "user": json.dumps({"id": 42, "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    correct_hash = _sign(fields, _BOT_TOKEN)
    tampered = dict(fields)
    tampered["user"] = json.dumps({"id": 99, "first_name": "Test"})  # changed after signing
    tampered["hash"] = correct_hash  # hash from the ORIGINAL payload
    assert verify_init_data(urlencode(tampered), _BOT_TOKEN, max_age_seconds=86400) is None


def test_wrong_bot_token_rejected():
    init_data = _build_init_data(user_id=42, bot_token=_BOT_TOKEN)
    assert verify_init_data(init_data, "a-different-token", max_age_seconds=86400) is None


def test_expired_auth_date_rejected():
    old_auth_date = int(time.time()) - 100_000
    init_data = _build_init_data(user_id=42, auth_date=old_auth_date)
    assert verify_init_data(init_data, _BOT_TOKEN, max_age_seconds=86400) is None


def test_missing_hash_rejected():
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})}
    assert verify_init_data(urlencode(fields), _BOT_TOKEN, max_age_seconds=86400) is None


def test_malformed_query_string_rejected():
    assert verify_init_data("noequalsatall", _BOT_TOKEN, max_age_seconds=86400) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_telegram_auth_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.telegram_auth'`

- [ ] **Step 3: Write `admin_panel/telegram_auth.py`**

```python
"""admin_panel.telegram_auth — Telegram Mini App initData verification.

Implements Telegram's official WebApp initData validation algorithm:
HMAC-SHA256 over the sorted, hash-excluded field set, keyed by
HMAC-SHA256("WebAppData", bot_token) — plus a freshness check on
auth_date. verify_init_data is a pure function; the FastAPI dependency
built on top of it is added in a later task.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

__all__ = ["verify_init_data"]


def verify_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None:
    """Validate a Telegram WebApp initData string; return its parsed fields or None.

    Returns None on any failure (bad signature, expired, malformed) —
    never raises on untrusted input.
    """
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw is None:
        return None
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        return None
    if time.time() - auth_date > max_age_seconds:
        return None
    result = dict(pairs)
    if "user" in result:
        try:
            result["user"] = json.loads(result["user"])
        except json.JSONDecodeError:
            return None
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_telegram_auth_verify.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add admin_panel/telegram_auth.py tests/test_telegram_auth_verify.py
git commit -m "feat(admin-panel): add verify_init_data, Telegram's official initData HMAC check"
```

---

### Task 4: `require_admin_auth` — FastAPI dependency restricting access to `admin_chat_id`

**Files:**
- Modify: `admin_panel/telegram_auth.py`
- Test: `tests/test_telegram_auth_dependency.py`

**Interfaces:**
- Consumes: `verify_init_data` (Task 3), `AUTH_MAX_AGE_SECONDS` (Task 2), `load_admin_chat_id` (Task 1), `settings.TELEGRAM_BOT_TOKEN` (pre-existing, `config/settings.py`).
- Produces: `admin_panel.telegram_auth.require_admin_auth` (async FastAPI dependency, raises `HTTPException(401)` or returns the parsed initData dict) — consumed by Task 7 (`admin_panel/server.py`).

- [ ] **Step 1: Write the failing tests**

`tests/test_telegram_auth_dependency.py`:

```python
"""tests.test_telegram_auth_dependency — admin_panel.telegram_auth.require_admin_auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import admin_panel.telegram_auth as auth_module

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "42"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _build_init_data(user_id: int, bot_token: str = _BOT_TOKEN) -> str:
    fields = {
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _client(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: _ADMIN_ID)
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth_module.require_admin_auth)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def test_valid_admin_init_data_allowed(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=42)})
    assert resp.status_code == 200


def test_missing_header_rejected(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/protected").status_code == 401


def test_wrong_user_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=99)})
    assert resp.status_code == 401


def test_invalid_signature_rejected(monkeypatch):
    client = _client(monkeypatch)
    bad = _build_init_data(user_id=42, bot_token="a-different-token")
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": bad})
    assert resp.status_code == 401


def test_no_admin_configured_rejects_everyone(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr(auth_module, "load_admin_chat_id", lambda: "")
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth_module.require_admin_auth)])
    async def protected():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/protected", headers={"X-Telegram-Init-Data": _build_init_data(user_id=42)})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_telegram_auth_dependency.py -v`
Expected: FAIL — `AttributeError: module 'admin_panel.telegram_auth' has no attribute 'require_admin_auth'`

- [ ] **Step 3: Append `require_admin_auth` to `admin_panel/telegram_auth.py`**

Add these imports at the top (alongside the existing ones):

```python
from fastapi import Header, HTTPException

from admin_panel.auth_config import AUTH_MAX_AGE_SECONDS
from config import settings
from config.admin_chat import load_admin_chat_id
```

Update `__all__` to `["verify_init_data", "require_admin_auth"]`.

Append this function at the end of the file:

```python
async def require_admin_auth(
    x_telegram_init_data: str | None = Header(default=None),
) -> dict:
    """FastAPI dependency: 401s unless x_telegram_init_data is valid AND belongs to admin_chat_id.

    Deliberately different from telegram_interface.bot's _is_admin: an
    unconfigured admin_chat_id here means DENY everyone, not allow everyone
    — this route is potentially internet-reachable via the tunnel, unlike
    the bot's own chat-based check.
    """
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="missing X-Telegram-Init-Data header")
    parsed = verify_init_data(x_telegram_init_data, settings.TELEGRAM_BOT_TOKEN, AUTH_MAX_AGE_SECONDS)
    if parsed is None:
        raise HTTPException(status_code=401, detail="invalid or expired initData")
    user = parsed.get("user")
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id or not isinstance(user, dict) or str(user.get("id")) != admin_chat_id:
        raise HTTPException(status_code=401, detail="unauthorized user")
    return parsed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_telegram_auth_dependency.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 6: Commit**

```bash
git add admin_panel/telegram_auth.py tests/test_telegram_auth_dependency.py
git commit -m "feat(admin-panel): add require_admin_auth FastAPI dependency restricted to admin_chat_id"
```

---

### Task 5: `[tunnel]` config section

**Files:**
- Create: `admin_panel/tunnel_config.py`
- Modify: `config/admin_panel.toml`
- Test: `tests/test_tunnel_config.py`

**Interfaces:**
- Produces: `admin_panel.tunnel_config.TUNNEL_ENABLED: bool`, `admin_panel.tunnel_config.TUNNEL_TIMEOUT_SECONDS: int` — consumed by Task 6 (`admin_panel/tunnel.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_tunnel_config.py`:

```python
"""tests.test_tunnel_config — admin_panel.tunnel_config constants + toml override."""
from __future__ import annotations

from admin_panel.tunnel_config import TUNNEL_ENABLED, TUNNEL_TIMEOUT_SECONDS


def test_defaults():
    assert TUNNEL_ENABLED is False
    assert TUNNEL_TIMEOUT_SECONDS == 15


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.tunnel_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text("[tunnel]\nenabled = true\ntimeout_seconds = 30\n")
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("tunnel", {})
    assert cfg["enabled"] is True
    assert cfg["timeout_seconds"] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_tunnel_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.tunnel_config'`

- [ ] **Step 3: Add `[tunnel]` to `config/admin_panel.toml`**

Append to the file (after `[server]`, before or after `[auth]` — order doesn't matter):

```toml

[tunnel]
# Cloudflare Quick Tunnel — exposes the panel as a Telegram Mini App over a
# fresh *.trycloudflare.com URL on every restart. Opt-in: enabling this
# changes the panel's security posture from "loopback only" to "internet
# reachable, gated by Telegram initData auth" (see
# docs/superpowers/specs/2026-08-13-admin-panel-miniapp-tunnel-design.md).
enabled = false
# Seconds to wait for cloudflared to print the assigned URL before giving up.
timeout_seconds = 15
```

- [ ] **Step 4: Write `admin_panel/tunnel_config.py`**

```python
"""admin_panel.tunnel_config — Cloudflare tunnel config. Reads config/admin_panel.toml ([tunnel] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "tunnel": {
        "enabled": False,
        "timeout_seconds": 15,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("tunnel", _DEFAULTS["tunnel"])

TUNNEL_ENABLED: Final[bool] = bool(_cfg.get("enabled", _DEFAULTS["tunnel"]["enabled"]))
TUNNEL_TIMEOUT_SECONDS: Final[int] = int(_cfg.get("timeout_seconds", _DEFAULTS["tunnel"]["timeout_seconds"]))

__all__ = ["TUNNEL_ENABLED", "TUNNEL_TIMEOUT_SECONDS"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_tunnel_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add admin_panel/tunnel_config.py config/admin_panel.toml tests/test_tunnel_config.py
git commit -m "feat(admin-panel): add [tunnel] config section for Cloudflare Quick Tunnel"
```

---

### Task 6: `admin_panel/tunnel.py` — launch `cloudflared`, parse the URL, update the chat menu button

**Files:**
- Create: `admin_panel/tunnel.py`
- Test: `tests/test_tunnel_extract_url.py`
- Test: `tests/test_tunnel_start_guards.py`

**Interfaces:**
- Consumes: `ADMIN_HOST`/`ADMIN_PORT` (`admin_panel/admin_config.py`, pre-existing), `TUNNEL_ENABLED`/`TUNNEL_TIMEOUT_SECONDS` (Task 5), `load_admin_chat_id` (Task 1).
- Produces: `admin_panel.tunnel.extract_tunnel_url(line: str) -> str | None`, `admin_panel.tunnel.start(application) -> None` (async, never raises) — consumed by Task 8 (`telegram_interface/_plugin_scanner.py`).

- [ ] **Step 1: Write the failing tests for `extract_tunnel_url`**

`tests/test_tunnel_extract_url.py`:

```python
"""tests.test_tunnel_extract_url — admin_panel.tunnel.extract_tunnel_url."""
from __future__ import annotations

from admin_panel.tunnel import extract_tunnel_url


def test_extracts_url_from_boxed_cloudflared_output():
    line = "|  https://random-example-words-1234.trycloudflare.com  |"
    assert extract_tunnel_url(line) == "https://random-example-words-1234.trycloudflare.com"


def test_extracts_url_from_plain_log_line():
    line = "2024-01-15T10:23:45Z INF https://foo-bar-baz.trycloudflare.com"
    assert extract_tunnel_url(line) == "https://foo-bar-baz.trycloudflare.com"


def test_returns_none_when_no_url_present():
    assert extract_tunnel_url("2024-01-15T10:23:45Z INF Starting tunnel") is None


def test_ignores_non_trycloudflare_https_urls():
    assert extract_tunnel_url("see https://example.com for docs") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tunnel_extract_url.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.tunnel'`

- [ ] **Step 3: Write `admin_panel/tunnel.py`**

```python
"""admin_panel.tunnel — Cloudflare Quick Tunnel launcher + chat menu button updater.

Exposes the admin panel (127.0.0.1, see admin_panel.server) as a Telegram
Mini App: launches `cloudflared tunnel --url ...` as a subprocess, parses
the auto-assigned *.trycloudflare.com URL from its output, then updates the
bot's chat menu button to open that URL. Opt-in via [tunnel] enabled in
config/admin_panel.toml; every failure degrades to a logged WARNING — the
tunnel is a convenience layered on top of the panel, never a hard
dependency of bot startup.
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from telegram import MenuButtonWebApp, WebAppInfo

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.tunnel_config import TUNNEL_ENABLED, TUNNEL_TIMEOUT_SECONDS
from config.admin_chat import load_admin_chat_id
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

log = get_logger(__name__)
__all__ = ["extract_tunnel_url", "start"]

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_tunnel_url(line: str) -> str | None:
    """Return the *.trycloudflare.com URL in ``line``, or None if absent."""
    match = _TUNNEL_URL_RE.search(line)
    return match.group(0) if match else None


async def start(application: "Application") -> None:
    """Launch the Cloudflare Quick Tunnel and keep it running. Never raises.

    Keeps draining cloudflared's stderr for the life of the tunnel process,
    not just until the URL is found — otherwise the pipe buffer fills once
    cloudflared's own periodic log lines exceed it, blocking the tunnel.
    """
    if not TUNNEL_ENABLED:
        return
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id:
        log.warning("tunnel enabled but no admin_chat_id configured in goat2.toml — skipping")
        return
    try:
        process = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://{ADMIN_HOST}:{ADMIN_PORT}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.warning("cloudflared not available, tunnel disabled: %s", exc)
        return

    assert process.stderr is not None
    url_found = False
    while True:
        try:
            raw = await asyncio.wait_for(
                process.stderr.readline(),
                timeout=TUNNEL_TIMEOUT_SECONDS if not url_found else None,
            )
        except asyncio.TimeoutError:
            log.warning("cloudflared did not report a tunnel URL within %ds", TUNNEL_TIMEOUT_SECONDS)
            return
        if not raw:
            log.warning("cloudflared exited%s", "" if url_found else " before reporting a tunnel URL")
            return
        if url_found:
            continue  # keep draining so the pipe never fills
        url = extract_tunnel_url(raw.decode(errors="replace"))
        if url is None:
            continue
        url_found = True
        log.info("admin panel tunnel ready: %s", url)
        try:
            await application.bot.set_chat_menu_button(
                chat_id=int(admin_chat_id),
                menu_button=MenuButtonWebApp(text="Admin Panel", web_app=WebAppInfo(url=url)),
            )
        except Exception as exc:  # noqa: BLE001 — menu button update is best-effort
            log.warning("failed to update chat menu button: %s", exc)
```

- [ ] **Step 4: Run the `extract_tunnel_url` tests to verify they pass**

Run: `python3 -m pytest tests/test_tunnel_extract_url.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for `start`'s guard clauses**

`tests/test_tunnel_start_guards.py`:

```python
"""tests.test_tunnel_start_guards — admin_panel.tunnel.start's no-op guard clauses.

The real subprocess-launch + Telegram-API path is not practically
unit-testable (external process, real Bot API) — it's covered by the
manual smoke test task at the end of this plan, the same way
admin_panel.server.start's real socket-bind behavior was smoke-tested
rather than unit-tested where a real dependency was unavoidable.
"""
from __future__ import annotations

import pytest

import admin_panel.tunnel as tunnel_module


class _FakeApplication:
    pass


@pytest.mark.asyncio
async def test_start_noop_when_tunnel_disabled(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", False)
    # Must return immediately without touching application.bot or spawning a process.
    await tunnel_module.start(_FakeApplication())


@pytest.mark.asyncio
async def test_start_warns_and_noop_when_no_admin_chat_id(monkeypatch):
    monkeypatch.setattr(tunnel_module, "TUNNEL_ENABLED", True)
    monkeypatch.setattr(tunnel_module, "load_admin_chat_id", lambda: "")
    await tunnel_module.start(_FakeApplication())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tunnel_start_guards.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Run the full test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 8: Commit**

```bash
git add admin_panel/tunnel.py tests/test_tunnel_extract_url.py tests/test_tunnel_start_guards.py
git commit -m "feat(admin-panel): add cloudflared tunnel launcher + chat menu button updater"
```

---

### Task 7: Placeholder page + apply `require_admin_auth` to `/api/*` in `create_app`

**Files:**
- Create: `admin_panel/routes/index.py`
- Create: `admin_panel/static/index.html`
- Modify: `admin_panel/server.py`
- Modify: `tests/test_admin_panel_server.py`

**Interfaces:**
- Consumes: `require_admin_auth` (Task 4).
- Produces: `GET /` serving the placeholder page (unauthenticated); every `/api/*` route now requires `require_admin_auth`.

- [ ] **Step 1: Create the placeholder page**

`admin_panel/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GOAT 2.0 Admin Panel</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
</head>
<body>
<pre id="out">loading…</pre>
<script>
  const tg = window.Telegram.WebApp;
  tg.ready();
  fetch("/api/metrics", {
    headers: { "X-Telegram-Init-Data": tg.initData }
  })
    .then(r => r.json())
    .then(data => { document.getElementById("out").textContent = JSON.stringify(data, null, 2); })
    .catch(err => { document.getElementById("out").textContent = "Error: " + err; });
</script>
</body>
</html>
```

- [ ] **Step 2: Create `admin_panel/routes/index.py`**

```python
"""admin_panel.routes.index — serves the Mini App placeholder page. No auth required (no sensitive data)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_INDEX_FILE = Path(__file__).parent.parent / "static" / "index.html"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(_INDEX_FILE)
```

- [ ] **Step 3: Write the failing test for the updated `create_app`**

Replace the contents of `tests/test_admin_panel_server.py` with (this preserves the two existing `start()` tests unchanged and updates `test_create_app_wires_all_routers` + adds a new unauthenticated-rejection test):

```python
"""tests.test_admin_panel_server — create_app wires all routers together, with
auth enforced on /api/* and the placeholder page open."""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import admin_panel.admin_config as admin_config
import admin_panel.server as server_module
from admin_panel.server import create_app, start

_BOT_TOKEN = "123456:test-bot-token"
_ADMIN_ID = "777"


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _auth_headers() -> dict:
    fields = {
        "user": json.dumps({"id": int(_ADMIN_ID), "first_name": "Test"}),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, _BOT_TOKEN)
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _FakeAnalytics:
    def get_report(self):
        return {"total_requests": 0}


class _FakePermanent:
    async def get_all_facts(self):
        return {}


class _FakeWorking:
    async def get_messages(self, chat_id):
        return []

    async def list_chat_ids(self):
        return []


class _FakeEpisodic:
    async def get_recent(self, chat_id, limit=20):
        return []

    async def get_oldest(self, limit, chat_id=None):
        return []

    async def list_chat_ids(self):
        return []


class _FakeRegistry:
    def __init__(self):
        self.memory_analytics = _FakeAnalytics()
        self.permanent_memory = _FakePermanent()
        self.working_memory = _FakeWorking()
        self.episodic_memory = _FakeEpisodic()


def test_index_page_is_not_authenticated():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/").status_code == 200


def test_create_app_wires_all_routers_with_auth(monkeypatch):
    monkeypatch.setattr("config.settings.TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setattr("admin_panel.telegram_auth.load_admin_chat_id", lambda: _ADMIN_ID)
    client = TestClient(create_app(_FakeRegistry()))
    headers = _auth_headers()
    assert client.get("/api/metrics", headers=headers).status_code == 200
    assert client.get("/api/logs", headers=headers).status_code == 200
    assert client.get("/api/memory/facts", headers=headers).status_code == 200
    assert client.get("/api/memory/working/chat1", headers=headers).status_code == 200
    assert client.get("/api/memory/episodic/chat1", headers=headers).status_code == 200
    assert client.get("/api/conversations", headers=headers).status_code == 200
    assert client.get("/api/conversations/chat1", headers=headers).status_code == 200


def test_api_routes_reject_unauthenticated_requests():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/api/metrics").status_code == 401


@pytest.mark.asyncio
async def test_start_never_raises_when_create_app_fails(monkeypatch):
    def _boom(_registry):
        raise RuntimeError("router registration exploded")

    monkeypatch.setattr(server_module, "create_app", _boom)

    # Must not raise — start() is documented to never propagate exceptions,
    # even ones that occur before uvicorn.Server._serve() is reached.
    await start(_FakeRegistry())


@pytest.mark.asyncio
async def test_start_never_raises_when_port_already_bound(monkeypatch):
    # Regression test for the SystemExit gap: uvicorn's Server.startup()
    # doesn't raise OSError when the bind fails — it catches it internally
    # and calls sys.exit(1), which raises SystemExit (a BaseException, not
    # an Exception). A plain `except Exception` in start() would miss this
    # entirely and let SystemExit propagate up into python-telegram-bot's
    # own (KeyboardInterrupt, SystemExit) handler, silently killing the
    # whole bot. Bind a real socket first so the admin panel's own attempt
    # to bind the same host:port actually fails the way it would in prod.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind((admin_config.ADMIN_HOST, 0))
        blocker.listen(1)
        bound_port = blocker.getsockname()[1]

        monkeypatch.setattr(server_module, "ADMIN_PORT", bound_port)

        # Must not raise — this is the exact scenario finding #1 fixes.
        await start(_FakeRegistry())
    finally:
        blocker.close()
```

- [ ] **Step 4: Run tests to verify the new/changed ones fail**

Run: `python3 -m pytest tests/test_admin_panel_server.py -v`
Expected: FAIL — `test_index_page_is_not_authenticated` fails with 404 (no `/` route yet); `test_api_routes_reject_unauthenticated_requests` fails because routes currently return 200 with no auth applied

- [ ] **Step 5: Update `admin_panel/server.py`**

Replace the contents of `admin_panel/server.py` with:

```python
"""admin_panel.server — FastAPI app + embedded uvicorn server for the GOAT 2.0 admin panel.

Runs read-only HTTP routes over the live ServiceRegistry, started as a
background asyncio task from the bot's post_init hook (same event loop, same
registry instance — metrics reflect the bot's real live state, and Redis/
Chroma/Letta clients stay bound to the one loop they were created on).

Every /api/* route requires require_admin_auth (Telegram initData, restricted
to admin_chat_id) — this matters once [tunnel] enabled makes the panel
reachable over the internet, not just from localhost. The index page is
exempt: it carries no sensitive data, only the JS that will itself attach
initData to its own API calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn
from fastapi import Depends, FastAPI

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.routes import conversations, index, logs, memory, metrics
from admin_panel.telegram_auth import require_admin_auth
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["create_app", "start"]


def create_app(registry: "ServiceRegistry") -> FastAPI:
    """Build the FastAPI app, wiring ``registry`` into app.state for every route."""
    app = FastAPI(title="GOAT 2.0 Admin Panel")
    app.state.registry = registry
    app.include_router(index.router)
    auth = [Depends(require_admin_auth)]
    app.include_router(metrics.router, dependencies=auth)
    app.include_router(logs.router, dependencies=auth)
    app.include_router(memory.router, dependencies=auth)
    app.include_router(conversations.router, dependencies=auth)
    return app


async def start(registry: "ServiceRegistry") -> None:
    """Start the admin server in the current event loop. Never raises.

    Calls ``Server._serve()`` directly instead of the public ``serve()`` —
    ``serve()`` unconditionally installs its own SIGINT/SIGTERM handlers via
    ``capture_signals()``, which would clobber python-telegram-bot's own
    shutdown handling since both run in the same process's main thread.
    ``_serve()`` is the same coroutine minus that signal capture.

    Catches ``SystemExit`` in addition to ``Exception``: uvicorn's
    ``Server.startup()`` does not raise ``OSError`` on a bind failure (e.g.
    port already in use) — it catches that internally and calls
    ``sys.exit(1)`` instead. ``SystemExit`` derives from ``BaseException``,
    so it would otherwise skip a plain ``except Exception`` and propagate up
    through python-telegram-bot's own top-level ``(KeyboardInterrupt,
    SystemExit)`` handler, silently shutting down the entire bot. We
    deliberately do NOT catch bare ``BaseException`` here, since that would
    also swallow ``asyncio.CancelledError``, which PTB's own shutdown relies
    on being able to propagate through tasks it cancels.
    """
    try:
        app = create_app(registry)
        config = uvicorn.Config(app, host=ADMIN_HOST, port=ADMIN_PORT, log_level="warning")
        server = uvicorn.Server(config)
        await server._serve()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — SystemExit: uvicorn's startup() sys.exit(1)s on bind failure; must never take the bot down with it
        log.warning("admin panel server stopped (%s:%d): %s", ADMIN_HOST, ADMIN_PORT, exc)
```

(The only changes from the current file: the `index` import, the `Depends`/`require_admin_auth` import, and `index.router` + `dependencies=auth` added in `create_app`. `start()` is unchanged.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_admin_panel_server.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Run the full test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 8: Commit**

```bash
git add admin_panel/routes/index.py admin_panel/static/index.html admin_panel/server.py tests/test_admin_panel_server.py
git commit -m "feat(admin-panel): serve Mini App placeholder page, require initData auth on /api/*"
```

---

### Task 8: Wire the tunnel into the bot's startup

**Files:**
- Modify: `telegram_interface/_plugin_scanner.py`
- Test: `tests/test_plugin_scanner_admin_wiring.py`

**Interfaces:**
- Consumes: `admin_panel.tunnel.start` (Task 6).
- Produces: `post_init_hook(registry)` now also schedules the tunnel as a background task, guarded identically to the admin panel server.

- [ ] **Step 1: Write the failing test (append to the existing wiring test file)**

Append this test function to `tests/test_plugin_scanner_admin_wiring.py` (keep the existing `test_post_init_schedules_admin_panel_start` test and its imports/fakes unchanged):

```python
def test_post_init_schedules_admin_tunnel_start(monkeypatch):
    import admin_panel.tunnel as tunnel_mod

    calls = []

    async def _fake_admin_start(registry):
        pass

    async def _fake_tunnel_start(application):
        calls.append(application)

    async def _fake_loop(registry):
        pass

    monkeypatch.setattr(admin_server_mod, "start", _fake_admin_start)
    monkeypatch.setattr(tunnel_mod, "start", _fake_tunnel_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    hook = mod.post_init_hook(fake_registry)
    fake_application = object()

    async def _run():
        await hook(fake_application)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_application]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_plugin_scanner_admin_wiring.py -v`
Expected: FAIL — the new test fails because `_post_init` never schedules a tunnel start (`calls == []`, not `[fake_application]`)

- [ ] **Step 3: Modify `telegram_interface/_plugin_scanner.py`**

Change the end of `_post_init` from:

```python
        asyncio.create_task(_loop(registry))
        try:
            from admin_panel.server import start as _start_admin_panel
            asyncio.create_task(_start_admin_panel(registry))
        except Exception as exc:  # noqa: BLE001 — panel is optional, bot startup is not
            log.warning("admin panel unavailable: %s", exc)
    return _post_init
```

to:

```python
        asyncio.create_task(_loop(registry))
        try:
            from admin_panel.server import start as _start_admin_panel
            asyncio.create_task(_start_admin_panel(registry))
        except Exception as exc:  # noqa: BLE001 — panel is optional, bot startup is not
            log.warning("admin panel unavailable: %s", exc)
        try:
            from admin_panel.tunnel import start as _start_admin_tunnel
            asyncio.create_task(_start_admin_tunnel(application))
        except Exception as exc:  # noqa: BLE001 — tunnel is optional, bot startup is not
            log.warning("admin panel tunnel unavailable: %s", exc)
    return _post_init
```

Also update the module docstring's second paragraph (currently describing only the admin panel server) to mention the tunnel too — append one sentence noting that `admin_panel.tunnel` is scheduled the same way, for the same reason.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_plugin_scanner_admin_wiring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 6: Commit**

```bash
git add telegram_interface/_plugin_scanner.py tests/test_plugin_scanner_admin_wiring.py
git commit -m "feat(admin-panel): start the Cloudflare tunnel from the bot's post_init hook"
```

---

### Task 9: Manual smoke test

Not unit-testable: requires the real `cloudflared` binary and (for the menu-button step) a real Telegram Bot API call. As with the base admin-panel-backend plan's Task 10, this runs in an isolated standalone script — never inside the live production bot process (`telegram_interface`, already running) — to avoid a second Telegram poller conflicting with it and to avoid mutating the live bot's chat menu button from a throwaway test run.

**Files:** none (verification only).

- [ ] **Step 1: Enable the tunnel in a scratch config**

In a scratch copy of the repo (or by temporarily setting `[tunnel] enabled = true` in `config/admin_panel.toml` — revert after this task), confirm `cloudflared --version` works (already verified available on this machine).

- [ ] **Step 2: Run a standalone script exercising the real tunnel, with a FAKE Telegram bot**

Write a script (in the scratchpad directory, not committed) that:
1. Builds the same fake registry used in `tests/test_admin_panel_server.py` and starts `admin_panel.server.start(registry)` as a background task (as in the base plan's Task 10 smoke test).
2. Builds a fake `application` object whose `.bot.set_chat_menu_button(**kwargs)` is an `async def` that just records its call arguments instead of calling the real Telegram API.
3. Sets `admin_panel.tunnel.TUNNEL_ENABLED = True` and `admin_panel.tunnel.load_admin_chat_id` to return a fake numeric string, then calls `admin_panel.tunnel.start(application)` as a background task.
4. Waits a few seconds, then asserts: the fake `set_chat_menu_button` was called exactly once, with a `menu_button.web_app.url` matching `https://*.trycloudflare.com`.
5. Curls that real `https://*.trycloudflare.com` URL directly (e.g. `curl -s https://<assigned>.trycloudflare.com/`) and confirms it proxies through to the local admin panel's `/` route (200, contains "GOAT 2.0 Admin Panel").
6. Sends SIGINT to the script's process and confirms both the admin server and the `cloudflared` subprocess terminate (no orphaned `cloudflared` process left running — check with `pgrep cloudflared` after).

- [ ] **Step 3: Verify auth actually gates the tunneled endpoint**

While the tunnel from Step 2 is still up, `curl` the tunnel's `/api/metrics` with no header and confirm `401`; then construct a valid signed `X-Telegram-Init-Data` header (using the same signing helper pattern as the unit tests, with the real bot token from `.env` and the fake admin id used in Step 2) and confirm `200`.

- [ ] **Step 4: Report result to the user**

No commit for this task — report the smoke-test outcome (menu button call captured correctly, tunnel URL reachable and proxying, auth gate confirmed working end-to-end, clean shutdown with no orphaned `cloudflared` process) before considering this plan complete. Revert any scratch config changes made in Step 1.

---

## Self-Review Notes

- **Spec coverage:** Quick Tunnel launch + URL parsing (Task 6), chat menu button auto-update (Task 6), `initData` HMAC verification (Task 3), uniform `/api/*` auth restricted to `admin_chat_id` (Task 4, Task 7), `[tunnel] enabled = false` opt-in default (Task 5), placeholder page proving the pipeline (Task 7), guarded/never-blocks-bot-startup wiring (Task 8) — every design doc section has a corresponding task.
- **Placeholder scan:** no TBD/TODO; every step has literal, runnable code.
- **Type consistency checked:** `verify_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict | None` signature is identical across Task 3's tests, Task 4's `require_admin_auth`, and this doc's design section. `extract_tunnel_url(line: str) -> str | None` and `start(application) -> None` names match between Task 6's implementation and Task 8's import (`from admin_panel.tunnel import start as _start_admin_tunnel`). `load_admin_chat_id` name matches between Task 1's implementation and Tasks 4/6's usage.
- **Out of scope, confirmed absent from this plan:** no Named Tunnel/custom domain support, no auto-reconnect on `cloudflared` crash, no in-chat "panel is ready" notification, no changes to the panel's read-only/no-mutation guarantee, no work on the full React frontend (separate plan).
