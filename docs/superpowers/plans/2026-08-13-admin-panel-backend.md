# Admin Panel Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, local-only FastAPI HTTP API — embedded in the existing Telegram bot process — exposing GOAT 2.0's memory metrics, logs, L1/L2/L3 memory tiers, and per-chat conversation timelines.

**Architecture:** FastAPI app started as a background `asyncio.create_task` from the bot's existing `post_init` hook (same event loop, same `ServiceRegistry` instance as the Telegram bot). Routes are thin — they call straight through to already-existing methods on `registry.permanent_memory` / `registry.working_memory` / `registry.episodic_memory` / `registry.memory_analytics`, no new retrieval/analytics logic. One new data-access method is added (`EpisodicQueries.list_chat_ids()`) because nothing today enumerates distinct chat_ids in ChromaDB. Full design: `docs/superpowers/specs/2026-08-13-admin-panel-design.md`.

**Tech Stack:** FastAPI 0.136+, uvicorn 0.41+ (both already installed locally; being added to `requirements.txt`), Python stdlib `tomllib` for config (matching the codebase's existing per-module `*_config.py` pattern).

**Scope note:** This plan covers the backend API only. The React+Vite frontend (Dashboard/Logs/Memory/Conversations pages) that consumes this API is a separate, independent implementation plan, written after this one lands — the backend is fully useful and testable on its own (`curl localhost:8765/api/...`) without it.

## Global Constraints

- File-size rule: every new/modified file stays ≤ 260 lines, single responsibility.
- Read-only: no route may mutate Redis/ChromaDB/Letta state.
- Local-only: server binds to `127.0.0.1`, never `0.0.0.0`; no authentication (explicit v1 scope, per design doc).
- The admin server must never crash or block the Telegram bot — every failure mode (port in use, backend tier down) is caught and logged, never raised into the bot's own startup/runtime path.
- No new data-access/retrieval logic — routes call existing public methods only, except the one new `list_chat_ids()` method explicitly justified in the design doc.
- Follow the existing `*_config.py` + `config/*.toml` loader pattern exactly (see `telegram_interface/telegram_config.py` / `config/telegram.toml` as the template).
- Tests use local fakes/doubles (no live Redis/ChromaDB/Letta required to run the suite), matching the existing convention in `tests/test_episodic_queries.py` and `tests/test_telegram_config.py`.

---

### Task 1: Admin panel config loader

**Files:**
- Create: `config/admin_panel.toml`
- Create: `admin_panel/__init__.py`
- Create: `admin_panel/admin_config.py`
- Test: `tests/test_admin_config.py`

**Interfaces:**
- Produces: `admin_panel.admin_config.ADMIN_HOST: str`, `admin_panel.admin_config.ADMIN_PORT: int` — consumed by Task 8 (`admin_panel/server.py`).

- [ ] **Step 1: Create the package init**

`admin_panel/__init__.py`:

```python
"""admin_panel — read-only local HTTP admin panel for GOAT 2.0, embedded in the bot process."""
```

- [ ] **Step 2: Create the config toml**

`config/admin_panel.toml`:

```toml
[server]
# Bind address for the admin panel HTTP server. Loopback-only by design —
# the panel is not intended for LAN/internet exposure (see
# docs/superpowers/specs/2026-08-13-admin-panel-design.md).
host = "127.0.0.1"
port = 8765
```

- [ ] **Step 3: Write the failing test**

`tests/test_admin_config.py`:

```python
"""tests.test_admin_config — admin_panel.admin_config constants + toml override."""
from __future__ import annotations

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT


def test_defaults():
    assert ADMIN_HOST == "127.0.0.1"
    assert ADMIN_PORT == 8765


def test_load_reads_toml_override(tmp_path, monkeypatch):
    import admin_panel.admin_config as mod

    toml_path = tmp_path / "admin_panel.toml"
    toml_path.write_text('[server]\nhost = "0.0.0.0"\nport = 9999\n')
    monkeypatch.setattr(mod, "_CONFIG_PATH", toml_path)
    cfg = mod._load().get("server", {})
    assert cfg["host"] == "0.0.0.0"
    assert cfg["port"] == 9999
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python3 -m pytest tests/test_admin_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.admin_config'`

- [ ] **Step 5: Write the implementation**

`admin_panel/admin_config.py`:

```python
"""admin_panel.admin_config — admin panel server config. Reads config/admin_panel.toml ([server] section)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "admin_panel.toml"

_DEFAULTS: dict = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("server", _DEFAULTS["server"])

ADMIN_HOST: Final[str] = str(_cfg.get("host", _DEFAULTS["server"]["host"]))
ADMIN_PORT: Final[int] = int(_cfg.get("port", _DEFAULTS["server"]["port"]))

__all__ = ["ADMIN_HOST", "ADMIN_PORT"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add config/admin_panel.toml admin_panel/__init__.py admin_panel/admin_config.py tests/test_admin_config.py
git commit -m "feat(admin-panel): add admin panel config loader"
```

---

### Task 2: Shared log-tail helper (extracted from the get_recent_logs tool)

**Files:**
- Create: `utils/logging/tail.py`
- Modify: `tools/goat_skills/get_recent_logs.py` (full replacement, behavior-preserving)
- Test: `tests/test_log_tail.py`
- Test: `tests/test_get_recent_logs_tool.py`

**Interfaces:**
- Produces: `utils.logging.tail.tail_log(path: Path, minutes: int, level: str, limit: int, max_lines: int) -> list[str]` — raises `ValueError` (unknown level), `FileNotFoundError` (missing log file), `OSError` (read failure). Consumed by Task 5 (`admin_panel/routes/logs.py`) and by the refactored `get_recent_logs` tool in this task.

- [ ] **Step 1: Write the failing tests for `tail_log`**

`tests/test_log_tail.py`:

```python
"""tests.test_log_tail — utils.logging.tail.tail_log, the shared log-tail helper
used by both the get_recent_logs tool and the admin panel's /api/logs route."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from utils.logging.tail import tail_log


def _write_log(path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _line(minutes_ago: int, level: str, msg: str) -> str:
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  {level:<8}  {msg}"


def test_returns_lines_within_window(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(5, "INFO", "recent"), _line(120, "INFO", "too old")])
    lines = tail_log(path, minutes=30, level="ALL", limit=100, max_lines=500)
    assert len(lines) == 1
    assert "recent" in lines[0]


def test_filters_by_level(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", "info line"), _line(1, "ERROR", "error line")])
    lines = tail_log(path, minutes=30, level="ERROR", limit=100, max_lines=500)
    assert len(lines) == 1
    assert "error line" in lines[0]


def test_caps_at_limit_keeping_newest(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(3, "INFO", "first"), _line(2, "INFO", "second"), _line(1, "INFO", "third")])
    lines = tail_log(path, minutes=30, level="ALL", limit=2, max_lines=500)
    assert len(lines) == 2
    assert "second" in lines[0] and "third" in lines[1]


def test_limit_is_capped_by_max_lines(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", f"line{i}") for i in range(5)])
    lines = tail_log(path, minutes=30, level="ALL", limit=1000, max_lines=3)
    assert len(lines) == 3


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        tail_log(tmp_path / "nope.log", minutes=30, level="ALL", limit=100, max_lines=500)


def test_unknown_level_raises_value_error(tmp_path):
    path = tmp_path / "goat2.log"
    _write_log(path, [_line(1, "INFO", "x")])
    with pytest.raises(ValueError):
        tail_log(path, minutes=30, level="BOGUS", limit=100, max_lines=500)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_log_tail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.logging.tail'`

- [ ] **Step 3: Write `utils/logging/tail.py`**

```python
"""utils.logging.tail — shared log-tail logic for the get_recent_logs tool and
the admin panel's /api/logs route. One implementation of "read a log file,
filter by cutoff/level, cap line count" so the two never drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

LEVELS = frozenset({"ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

__all__ = ["LEVELS", "tail_log"]


def _level_matches(line: str, level: str) -> bool:
    """True when ``line`` carries level token ``level`` (ALL = everything)."""
    if not level or level.upper() == "ALL":
        return True
    return f" {level.upper()} " in line.upper()


def _parse_ts(line: str) -> datetime | None:
    """Parse the leading ``YYYY-MM-DDTHH:MM:SS`` timestamp, or None."""
    try:
        return datetime.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def tail_log(path: Path, minutes: int, level: str, limit: int, max_lines: int) -> list[str]:
    """Return matching log lines from the last ``minutes`` minutes, oldest-first.

    Raises:
        ValueError: ``level`` is not a recognized level.
        FileNotFoundError: ``path`` does not exist.
        OSError: ``path`` exists but could not be read.
    """
    lvl = (level or "ALL").upper()
    if lvl not in LEVELS:
        raise ValueError(f"unknown level {level!r}; expected one of {sorted(LEVELS - {'ALL'})} or ALL")
    if not path.exists():
        raise FileNotFoundError(str(path))
    cutoff = datetime.now() - timedelta(minutes=max(0, int(minutes)))
    cap = max(1, min(int(limit), max_lines))
    matched: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ts = _parse_ts(line)
            if ts is not None and ts < cutoff:
                continue
            if not _level_matches(line, lvl):
                continue
            matched.append(line.rstrip("\n"))
    if len(matched) > cap:
        matched = matched[-cap:]
    return matched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_log_tail.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write the characterization test for the tool (locks in current message format before refactor)**

`tests/test_get_recent_logs_tool.py`:

```python
"""tests.test_get_recent_logs_tool — goat_skills.get_recent_logs tool wraps
utils.logging.tail.tail_log into GOAT-facing message strings."""
from __future__ import annotations

import asyncio
from datetime import datetime

import tools.goat_skills.get_recent_logs as mod


class _FakeRegistry:
    pass


def _line(msg: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  INFO      {msg}"


def test_handler_returns_matching_lines(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hello") + "\n")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(minutes=30, level="ALL", limit=100))
    assert "hello" in result


def test_handler_missing_file_message(tmp_path, monkeypatch):
    path = tmp_path / "missing.log"
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler())
    assert "log file not found" in result


def test_handler_unknown_level_message(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hi") + "\n")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(level="BOGUS"))
    assert "unknown level" in result


def test_handler_no_matches_message(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text("")
    monkeypatch.setattr(mod, "LOG_FILE", path)
    tool = mod.build(_FakeRegistry())[0]
    result = asyncio.run(tool.handler(minutes=30))
    assert "no matching log lines" in result
```

- [ ] **Step 6: Run the new tool test against the OLD implementation to confirm it currently passes**

Run: `python3 -m pytest tests/test_get_recent_logs_tool.py -v`
Expected: PASS (4 tests) — this characterizes current behavior before the refactor.

- [ ] **Step 7: Refactor `tools/goat_skills/get_recent_logs.py` to use `tail_log`**

Full file replacement:

```python
"""goat_skills.get_recent_logs — on-demand live log-tail tool.

GOAT calls this when asked about its own recent logs, warnings, or errors.
Reads the exact file ``utils.logging.setup`` writes (via the shared ``LOG_FILE``
constant), returns lines from the last ``minutes`` minutes, optionally filtered
by level. On-demand only — no always-on context injection.

Tailing logic lives in ``utils.logging.tail.tail_log`` — shared with the admin
panel's ``/api/logs`` route so the two never drift.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE
from utils.logging.tail import tail_log

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]

_DESCRIPTION = (
    "Recent lines from GOAT's own log file, optionally filtered by level. "
    "Use this when the user asks to see recent logs, warnings, or errors, or "
    "what happened recently. Returns the last N minutes (default 30)."
)


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the get_recent_logs tool, reading the shared log file."""
    async def handler(minutes: int = 30, level: str = "ALL", limit: int = 100, chat_id: str = "") -> str:
        """Return matching log lines from the last ``minutes`` minutes."""
        try:
            matched = tail_log(LOG_FILE, minutes, level, limit, _MAX_LINES)
        except ValueError as exc:
            return f"({exc})"
        except FileNotFoundError:
            return f"(log file not found: {LOG_FILE})"
        except OSError as exc:
            return f"(error reading log file: {exc})"
        if not matched:
            return f"(no matching log lines in the last {minutes} minute(s))"
        return "\n".join(matched)

    return [ToolDefinition(
        name="get_recent_logs",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Look-back window in minutes (default 30)"},
                "level": {"type": "string", "description": "Filter: ALL/DEBUG/INFO/WARNING/ERROR/CRITICAL (default ALL)"},
                "limit": {"type": "integer", "description": f"Max lines to return (default 100, max {_MAX_LINES})"},
            },
        },
        handler=handler,
    )]
```

- [ ] **Step 8: Run both test files to verify the refactor preserved behavior**

Run: `python3 -m pytest tests/test_log_tail.py tests/test_get_recent_logs_tool.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 9: Commit**

```bash
git add utils/logging/tail.py tools/goat_skills/get_recent_logs.py tests/test_log_tail.py tests/test_get_recent_logs_tool.py
git commit -m "refactor(logs): extract shared tail_log helper from get_recent_logs tool"
```

---

### Task 3: `EpisodicMemory.list_chat_ids()`

**Files:**
- Modify: `memory/episodic/queries.py`
- Test: `tests/test_episodic_queries.py` (extend existing file)

**Interfaces:**
- Produces: `EpisodicMemory.list_chat_ids() -> list[str]` (async; sorted, deduped) — consumed by Task 6 (`admin_panel/routes/conversations.py`).

- [ ] **Step 1: Write the failing tests (append to the existing file)**

Append to `tests/test_episodic_queries.py`:

```python
def test_list_chat_ids_dedupes_and_sorts():
    e = _episodic([_entry(0, "b", "x", 1), _entry(1, "a", "y", 2), _entry(2, "a", "z", 3)])
    assert asyncio.run(e.list_chat_ids()) == ["a", "b"]


def test_list_chat_ids_empty_collection():
    e = _episodic([])
    assert asyncio.run(e.list_chat_ids()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_episodic_queries.py -v`
Expected: FAIL — `AttributeError: 'EpisodicMemory' object has no attribute 'list_chat_ids'`

- [ ] **Step 3: Add the method to `memory/episodic/queries.py`**

Add this method to the `EpisodicQueries` class (place it near `count`/`get_oldest`):

```python
    async def list_chat_ids(self) -> list[str]:
        """Return the distinct chat_ids present in the collection (metadata-only read).

        Cheaper than ``get_all_for_index`` — reads only ``metadatas``, never
        ``documents``, since only ``chat_id`` is needed. O(collection size),
        like the other bulk-read methods on this mixin.
        """
        results = await asyncio.to_thread(
            self._get_collection().get, include=["metadatas"],
        )
        metas = results.get("metadatas") or []
        return sorted({m.get("chat_id") for m in metas if m.get("chat_id")})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_episodic_queries.py -v`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add memory/episodic/queries.py tests/test_episodic_queries.py
git commit -m "feat(memory): add EpisodicMemory.list_chat_ids for admin panel chat discovery"
```

---

### Task 4: `/api/metrics` route + FastAPI/uvicorn dependency

**Files:**
- Modify: `requirements.txt`
- Create: `admin_panel/routes/__init__.py`
- Create: `admin_panel/routes/metrics.py`
- Test: `tests/test_admin_panel_metrics.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `admin_panel.routes.metrics.router` (a `fastapi.APIRouter` with `GET /api/metrics`) — consumed by Task 8 (`admin_panel/server.py`).

- [ ] **Step 1: Add fastapi/uvicorn to requirements.txt**

Append this section to `requirements.txt` (both are already installed locally; this just declares them):

```
# Admin panel — read-only local web UI embedded in the bot process
fastapi>=0.136.0        # admin_panel/server.py, admin_panel/routes/*
uvicorn>=0.41.0         # admin_panel/server.py — embedded ASGI server
```

- [ ] **Step 2: Create the routes package init**

`admin_panel/routes/__init__.py`:

```python
"""admin_panel.routes — read-only API routers for the admin panel."""
```

- [ ] **Step 3: Write the failing test**

`tests/test_admin_panel_metrics.py`:

```python
"""tests.test_admin_panel_metrics — /api/metrics route over a fake MemoryAnalytics."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.metrics import router


class _FakeAnalytics:
    def get_report(self):
        return {"total_requests": 5, "cache_hit_rate": 0.5}


class _FakeRegistry:
    def __init__(self):
        self.memory_analytics = _FakeAnalytics()


def _client():
    app = FastAPI()
    app.state.registry = _FakeRegistry()
    app.include_router(router)
    return TestClient(app)


def test_get_metrics_returns_report():
    resp = _client().get("/api/metrics")
    assert resp.status_code == 200
    assert resp.json() == {"total_requests": 5, "cache_hit_rate": 0.5}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python3 -m pytest tests/test_admin_panel_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.routes.metrics'`

- [ ] **Step 5: Write `admin_panel/routes/metrics.py`**

```python
"""admin_panel.routes.metrics — read-only /api/metrics over the live MemoryAnalytics report."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/metrics")
async def get_metrics(request: Request) -> dict:
    registry = request.app.state.registry
    return registry.memory_analytics.get_report()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_panel_metrics.py -v`
Expected: PASS (1 test)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt admin_panel/routes/__init__.py admin_panel/routes/metrics.py tests/test_admin_panel_metrics.py
git commit -m "feat(admin-panel): add /api/metrics route"
```

---

### Task 5: `/api/logs` route

**Files:**
- Create: `admin_panel/routes/logs.py`
- Test: `tests/test_admin_panel_logs.py`

**Interfaces:**
- Consumes: `utils.logging.tail.tail_log` (Task 2), `utils.logging.setup.LOG_FILE`, `tools.get_recent_logs_config.GET_RECENT_LOGS_MAX_LINES`.
- Produces: `admin_panel.routes.logs.router` (`GET /api/logs`) — consumed by Task 8.

- [ ] **Step 1: Write the failing test**

`tests/test_admin_panel_logs.py`:

```python
"""tests.test_admin_panel_logs — /api/logs route over a temp log file."""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_panel.routes.logs as logs_route


def _line(msg: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}  some.module  INFO      {msg}"


def _client():
    app = FastAPI()
    app.include_router(logs_route.router)
    return TestClient(app)


def test_get_logs_returns_lines(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hello") + "\n")
    monkeypatch.setattr(logs_route, "LOG_FILE", path)
    resp = _client().get("/api/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["lines"]) == 1
    assert "hello" in body["lines"][0]


def test_get_logs_missing_file_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(logs_route, "LOG_FILE", tmp_path / "missing.log")
    resp = _client().get("/api/logs")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_logs_unknown_level_returns_error(tmp_path, monkeypatch):
    path = tmp_path / "goat2.log"
    path.write_text(_line("hi") + "\n")
    monkeypatch.setattr(logs_route, "LOG_FILE", path)
    resp = _client().get("/api/logs?level=BOGUS")
    assert resp.status_code == 200
    assert "error" in resp.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_admin_panel_logs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.routes.logs'`

- [ ] **Step 3: Write `admin_panel/routes/logs.py`**

```python
"""admin_panel.routes.logs — read-only /api/logs over the shared tail_log helper."""
from __future__ import annotations

from fastapi import APIRouter

from tools.get_recent_logs_config import GET_RECENT_LOGS_MAX_LINES as _MAX_LINES
from utils.logging.setup import LOG_FILE
from utils.logging.tail import tail_log

router = APIRouter()


@router.get("/api/logs")
async def get_logs(minutes: int = 30, level: str = "ALL", limit: int = 100) -> dict:
    try:
        lines = tail_log(LOG_FILE, minutes, level, limit, _MAX_LINES)
    except ValueError as exc:
        return {"error": str(exc)}
    except FileNotFoundError:
        return {"error": f"log file not found: {LOG_FILE}"}
    except OSError as exc:
        return {"error": f"error reading log file: {exc}"}
    return {"lines": lines}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_panel_logs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add admin_panel/routes/logs.py tests/test_admin_panel_logs.py
git commit -m "feat(admin-panel): add /api/logs route"
```

---

### Task 6: `/api/memory/*` routes (L1 facts, L2 working, L3 episodic)

**Files:**
- Create: `admin_panel/routes/memory.py`
- Test: `tests/test_admin_panel_memory.py`

**Interfaces:**
- Consumes: `registry.permanent_memory.get_all_facts()`, `registry.working_memory.get_messages(chat_id)`, `registry.episodic_memory.get_recent(chat_id, limit)` / `get_oldest(limit, chat_id)` (all pre-existing).
- Produces: `admin_panel.routes.memory.router` (`GET /api/memory/facts`, `GET /api/memory/working/{chat_id}`, `GET /api/memory/episodic/{chat_id}`) — consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

`tests/test_admin_panel_memory.py`:

```python
"""tests.test_admin_panel_memory — /api/memory/* routes over fake tier doubles."""
from __future__ import annotations

import httpx
import redis.exceptions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.memory import router


class _FakePermanent:
    def __init__(self, facts=None, raise_error=False):
        self._facts = facts or {}
        self._raise = raise_error

    async def get_all_facts(self):
        if self._raise:
            raise httpx.ConnectError("connection refused")
        return self._facts


class _FakeWorking:
    def __init__(self, messages=None, raise_error=False):
        self._messages = messages or {}
        self._raise = raise_error

    async def get_messages(self, chat_id):
        if self._raise:
            raise redis.exceptions.ConnectionError("connection refused")
        return self._messages.get(chat_id, [])


class _FakeEpisodic:
    def __init__(self, recent=None, oldest=None, raise_error=False):
        self._recent = recent or []
        self._oldest = oldest or []
        self._raise = raise_error

    async def get_recent(self, chat_id, limit=20):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._recent

    async def get_oldest(self, limit, chat_id=None):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._oldest


class _FakeRegistry:
    def __init__(self, permanent=None, working=None, episodic=None):
        self.permanent_memory = permanent or _FakePermanent()
        self.working_memory = working or _FakeWorking()
        self.episodic_memory = episodic or _FakeEpisodic()


def _client(registry):
    app = FastAPI()
    app.state.registry = registry
    app.include_router(router)
    return TestClient(app)


def test_get_facts_returns_facts():
    resp = _client(_FakeRegistry(permanent=_FakePermanent({"name": "Maria"}))).get("/api/memory/facts")
    assert resp.status_code == 200
    assert resp.json() == {"facts": {"name": "Maria"}}


def test_get_facts_letta_down_returns_error():
    resp = _client(_FakeRegistry(permanent=_FakePermanent(raise_error=True))).get("/api/memory/facts")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_working_returns_messages():
    msgs = {"chat1": [{"role": "user", "content": "hi", "timestamp": 1.0}]}
    resp = _client(_FakeRegistry(working=_FakeWorking(msgs))).get("/api/memory/working/chat1")
    assert resp.json() == {"messages": msgs["chat1"]}


def test_get_working_unknown_chat_returns_empty():
    resp = _client(_FakeRegistry()).get("/api/memory/working/nope")
    assert resp.json() == {"messages": []}


def test_get_working_redis_down_returns_error():
    resp = _client(_FakeRegistry(working=_FakeWorking(raise_error=True))).get("/api/memory/working/chat1")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_episodic_recent_default():
    entries = [{"content": "x", "metadata": {"timestamp": 1.0}}]
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(recent=entries))).get("/api/memory/episodic/chat1")
    assert resp.json() == {"entries": entries}


def test_get_episodic_oldest_order():
    entries = [{"id": "a", "content": "old", "metadata": {"timestamp": 1.0}}]
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(oldest=entries))).get("/api/memory/episodic/chat1?order=oldest")
    assert resp.json() == {"entries": entries}


def test_get_episodic_unknown_order_returns_error():
    resp = _client(_FakeRegistry()).get("/api/memory/episodic/chat1?order=sideways")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_get_episodic_chromadb_down_returns_error():
    resp = _client(_FakeRegistry(episodic=_FakeEpisodic(raise_error=True))).get("/api/memory/episodic/chat1")
    assert "error" in resp.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_admin_panel_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.routes.memory'`

- [ ] **Step 3: Write `admin_panel/routes/memory.py`**

```python
"""admin_panel.routes.memory — read-only L1/L2/L3 memory-browsing endpoints.

Each route calls straight through to the registry's existing tier objects
(permanent/working/episodic) — no new data-access logic. A backend being
down degrades only its own route, returned as {"error": ...} with HTTP 200
(this is a debug tool, not a critical path — a raw 500 would be misleading).
"""
from __future__ import annotations

import httpx
import redis.exceptions
from fastapi import APIRouter, Request

router = APIRouter()

_VALID_ORDERS = frozenset({"recent", "oldest"})


@router.get("/api/memory/facts")
async def get_facts(request: Request) -> dict:
    registry = request.app.state.registry
    try:
        facts = await registry.permanent_memory.get_all_facts()
    except httpx.HTTPError as exc:
        return {"error": f"Letta unavailable: {exc}"}
    return {"facts": facts}


@router.get("/api/memory/working/{chat_id}")
async def get_working(request: Request, chat_id: str) -> dict:
    registry = request.app.state.registry
    try:
        messages = await registry.working_memory.get_messages(chat_id)
    except redis.exceptions.RedisError as exc:
        return {"error": f"Redis unavailable: {exc}"}
    return {"messages": messages}


@router.get("/api/memory/episodic/{chat_id}")
async def get_episodic(request: Request, chat_id: str, limit: int = 50, order: str = "recent") -> dict:
    if order not in _VALID_ORDERS:
        return {"error": f"unknown order {order!r}; expected 'recent' or 'oldest'"}
    registry = request.app.state.registry
    try:
        if order == "oldest":
            entries = await registry.episodic_memory.get_oldest(limit, chat_id=chat_id)
        else:
            entries = await registry.episodic_memory.get_recent(chat_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 — ChromaDB has no single documented exception base
        return {"error": f"ChromaDB unavailable: {exc}"}
    return {"entries": entries}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_admin_panel_memory.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add admin_panel/routes/memory.py tests/test_admin_panel_memory.py
git commit -m "feat(admin-panel): add /api/memory/facts, /working/{chat_id}, /episodic/{chat_id} routes"
```

---

### Task 7: `/api/conversations` routes (list + per-chat timeline)

**Files:**
- Create: `admin_panel/routes/conversations.py`
- Test: `tests/test_admin_panel_conversations.py`

**Interfaces:**
- Consumes: `registry.working_memory.list_chat_ids()` (pre-existing), `registry.episodic_memory.list_chat_ids()` (Task 3), `registry.working_memory.get_messages(chat_id)`, `registry.episodic_memory.get_recent(chat_id, limit)`.
- Produces: `admin_panel.routes.conversations.router` (`GET /api/conversations`, `GET /api/conversations/{chat_id}`) — consumed by Task 8. Response contract note: unlike `memory.py`, these two routes return a top-level `warnings: list[str]` key (only present when non-empty) instead of `error`, because each response merges two independent backends (Redis + ChromaDB) and a single one failing should not blank out data the other successfully provided.

- [ ] **Step 1: Write the failing tests**

`tests/test_admin_panel_conversations.py`:

```python
"""tests.test_admin_panel_conversations — /api/conversations routes over fake tier doubles.

Unlike memory.py's routes, these merge two backends per response, so a single
backend failing surfaces as a `warnings` list alongside whatever data the
other backend provided — never a blanket {"error": ...}.
"""
from __future__ import annotations

import redis.exceptions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_panel.routes.conversations import router


class _FakeWorking:
    def __init__(self, chat_ids=None, messages=None, raise_error=False):
        self._chat_ids = chat_ids or []
        self._messages = messages or {}
        self._raise = raise_error

    async def list_chat_ids(self):
        if self._raise:
            raise redis.exceptions.ConnectionError("down")
        return self._chat_ids

    async def get_messages(self, chat_id):
        if self._raise:
            raise redis.exceptions.ConnectionError("down")
        return self._messages.get(chat_id, [])


class _FakeEpisodic:
    def __init__(self, chat_ids=None, recent=None, raise_error=False):
        self._chat_ids = chat_ids or []
        self._recent = recent or []
        self._raise = raise_error

    async def list_chat_ids(self):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._chat_ids

    async def get_recent(self, chat_id, limit=20):
        if self._raise:
            raise RuntimeError("chromadb down")
        return self._recent


class _FakeRegistry:
    def __init__(self, working=None, episodic=None):
        self.working_memory = working or _FakeWorking()
        self.episodic_memory = episodic or _FakeEpisodic()


def _client(registry):
    app = FastAPI()
    app.state.registry = registry
    app.include_router(router)
    return TestClient(app)


def test_list_conversations_unions_active_and_archived():
    working = _FakeWorking(chat_ids=["a", "b"])
    episodic = _FakeEpisodic(chat_ids=["b", "c"])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations")
    body = resp.json()
    assert "warnings" not in body
    statuses = {c["chat_id"]: c["status"] for c in body["conversations"]}
    assert statuses == {"a": "active", "b": "active", "c": "archived_only"}


def test_list_conversations_redis_down_still_returns_archived():
    working = _FakeWorking(raise_error=True)
    episodic = _FakeEpisodic(chat_ids=["c"])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations")
    body = resp.json()
    assert body["conversations"] == [{"chat_id": "c", "status": "archived_only"}]
    assert "warnings" in body and len(body["warnings"]) == 1


def test_list_conversations_both_down_returns_empty_with_two_warnings():
    resp = _client(_FakeRegistry(working=_FakeWorking(raise_error=True), episodic=_FakeEpisodic(raise_error=True))).get("/api/conversations")
    body = resp.json()
    assert body["conversations"] == []
    assert len(body["warnings"]) == 2


def test_get_conversation_merges_l2_and_l3_sorted_by_timestamp():
    working = _FakeWorking(messages={"chat1": [{"role": "user", "content": "hi", "timestamp": 2.0}]})
    episodic = _FakeEpisodic(recent=[{"content": "archived note", "metadata": {"timestamp": 1.0}}])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations/chat1")
    body = resp.json()
    assert body["chat_id"] == "chat1"
    assert [e["tier"] for e in body["timeline"]] == ["L3", "L2"]
    assert body["timeline"][0]["content"] == "archived note"
    assert body["timeline"][1]["content"] == "hi"


def test_get_conversation_partial_failure_still_returns_other_tier():
    working = _FakeWorking(raise_error=True)
    episodic = _FakeEpisodic(recent=[{"content": "archived note", "metadata": {"timestamp": 1.0}}])
    resp = _client(_FakeRegistry(working=working, episodic=episodic)).get("/api/conversations/chat1")
    body = resp.json()
    assert len(body["timeline"]) == 1
    assert "warnings" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_admin_panel_conversations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.routes.conversations'`

- [ ] **Step 3: Write `admin_panel/routes/conversations.py`**

```python
"""admin_panel.routes.conversations — read-only chat_id discovery + per-chat timeline.

Chat discovery unions two independent sources: active sessions in Redis (L2,
bounded by WORKING_TTL_SECONDS) and chats that only survive in ChromaDB (L3,
past their L2 TTL). Either source failing degrades to a `warnings` list on
the response rather than a single {"error": ...} — these two routes
intentionally still return whatever half succeeded, unlike memory.py's
single-tier routes.
"""
from __future__ import annotations

import redis.exceptions
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/conversations")
async def list_conversations(request: Request) -> dict:
    registry = request.app.state.registry
    warnings: list[str] = []
    active: list[str] = []
    all_episodic: list[str] = []
    try:
        active = await registry.working_memory.list_chat_ids()
    except redis.exceptions.RedisError as exc:
        warnings.append(f"Redis unavailable: {exc}")
    try:
        all_episodic = await registry.episodic_memory.list_chat_ids()
    except Exception as exc:  # noqa: BLE001 — ChromaDB has no single documented exception base
        warnings.append(f"ChromaDB unavailable: {exc}")
    archived = sorted(set(all_episodic) - set(active))
    conversations = (
        [{"chat_id": c, "status": "active"} for c in sorted(active)]
        + [{"chat_id": c, "status": "archived_only"} for c in archived]
    )
    result: dict = {"conversations": conversations}
    if warnings:
        result["warnings"] = warnings
    return result


@router.get("/api/conversations/{chat_id}")
async def get_conversation(request: Request, chat_id: str) -> dict:
    registry = request.app.state.registry
    warnings: list[str] = []
    l2: list[dict] = []
    l3: list[dict] = []
    try:
        l2 = await registry.working_memory.get_messages(chat_id)
    except redis.exceptions.RedisError as exc:
        warnings.append(f"Redis unavailable: {exc}")
    try:
        l3 = await registry.episodic_memory.get_recent(chat_id, limit=100)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ChromaDB unavailable: {exc}")
    timeline = [
        {"tier": "L2", "timestamp": m.get("timestamp", 0), "role": m.get("role", ""), "content": m.get("content", "")}
        for m in l2
    ] + [
        {"tier": "L3", "timestamp": e["metadata"].get("timestamp", 0), "role": "", "content": e["content"]}
        for e in l3
    ]
    timeline.sort(key=lambda entry: float(entry["timestamp"] or 0))
    result: dict = {"chat_id": chat_id, "timeline": timeline}
    if warnings:
        result["warnings"] = warnings
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_admin_panel_conversations.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add admin_panel/routes/conversations.py tests/test_admin_panel_conversations.py
git commit -m "feat(admin-panel): add /api/conversations routes"
```

---

### Task 8: `admin_panel/server.py` — app factory + embedded uvicorn start

**Files:**
- Create: `admin_panel/server.py`
- Test: `tests/test_admin_panel_server.py`

**Interfaces:**
- Consumes: `admin_panel.admin_config.ADMIN_HOST`/`ADMIN_PORT` (Task 1), all four routers from Tasks 4-7.
- Produces: `admin_panel.server.create_app(registry) -> FastAPI`, `admin_panel.server.start(registry) -> None` (async, never raises) — consumed by Task 9.

- [ ] **Step 1: Write the failing test**

`tests/test_admin_panel_server.py`:

```python
"""tests.test_admin_panel_server — create_app wires all four routers together."""
from __future__ import annotations

from fastapi.testclient import TestClient

from admin_panel.server import create_app


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


def test_create_app_wires_all_routers():
    client = TestClient(create_app(_FakeRegistry()))
    assert client.get("/api/metrics").status_code == 200
    assert client.get("/api/logs").status_code == 200
    assert client.get("/api/memory/facts").status_code == 200
    assert client.get("/api/memory/working/chat1").status_code == 200
    assert client.get("/api/memory/episodic/chat1").status_code == 200
    assert client.get("/api/conversations").status_code == 200
    assert client.get("/api/conversations/chat1").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_admin_panel_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin_panel.server'`

- [ ] **Step 3: Write `admin_panel/server.py`**

```python
"""admin_panel.server — FastAPI app + embedded uvicorn server for the GOAT 2.0 admin panel.

Runs read-only HTTP routes over the live ServiceRegistry, started as a
background asyncio task from the bot's post_init hook (same event loop, same
registry instance — metrics reflect the bot's real live state, and Redis/
Chroma/Letta clients stay bound to the one loop they were created on).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.routes import conversations, logs, memory, metrics
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["create_app", "start"]


def create_app(registry: "ServiceRegistry") -> FastAPI:
    """Build the FastAPI app, wiring ``registry`` into app.state for every route."""
    app = FastAPI(title="GOAT 2.0 Admin Panel")
    app.state.registry = registry
    app.include_router(metrics.router)
    app.include_router(logs.router)
    app.include_router(memory.router)
    app.include_router(conversations.router)
    return app


async def start(registry: "ServiceRegistry") -> None:
    """Start the admin server in the current event loop. Never raises.

    Calls ``Server._serve()`` directly instead of the public ``serve()`` —
    ``serve()`` unconditionally installs its own SIGINT/SIGTERM handlers via
    ``capture_signals()``, which would clobber python-telegram-bot's own
    shutdown handling since both run in the same process's main thread.
    ``_serve()`` is the same coroutine minus that signal capture.
    """
    app = create_app(registry)
    config = uvicorn.Config(app, host=ADMIN_HOST, port=ADMIN_PORT, log_level="warning")
    server = uvicorn.Server(config)
    try:
        await server._serve()
    except Exception as exc:  # noqa: BLE001 — must never take the bot down with it
        log.warning("admin panel server failed to start on %s:%d: %s", ADMIN_HOST, ADMIN_PORT, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_admin_panel_server.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add admin_panel/server.py tests/test_admin_panel_server.py
git commit -m "feat(admin-panel): add FastAPI app factory + embedded uvicorn start"
```

---

### Task 9: Wire the admin server into the bot's startup

**Files:**
- Modify: `telegram_interface/_plugin_scanner.py`
- Test: `tests/test_plugin_scanner_admin_wiring.py`

**Interfaces:**
- Consumes: `admin_panel.server.start` (Task 8).
- Produces: `post_init_hook(registry)` now also schedules the admin server as a background task (behavior change only, no new public interface).

- [ ] **Step 1: Write the failing test**

`tests/test_plugin_scanner_admin_wiring.py`:

```python
"""tests.test_plugin_scanner_admin_wiring — post_init_hook also schedules the
admin panel server as a background task, alongside the existing plugin scan loop."""
from __future__ import annotations

import asyncio

import telegram_interface._plugin_scanner as mod


class _FakeWarmup:
    async def warmup(self):
        pass


class _FakeRegistry:
    def __init__(self):
        self.episodic_memory = _FakeWarmup()
        self.bm25_index = _FakeWarmup()
        self.gliner_extractor = _FakeWarmup()
        self.reranker = None


def test_post_init_schedules_admin_panel_start(monkeypatch):
    calls = []

    async def _fake_start(registry):
        calls.append(registry)

    async def _fake_loop(registry):
        pass

    monkeypatch.setattr(mod, "_start_admin_panel", _fake_start)
    monkeypatch.setattr(mod, "_loop", _fake_loop)

    fake_registry = _FakeRegistry()
    hook = mod.post_init_hook(fake_registry)

    async def _run():
        await hook(None)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [fake_registry]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_plugin_scanner_admin_wiring.py -v`
Expected: FAIL — `AttributeError: <module 'telegram_interface._plugin_scanner'> does not have the attribute '_start_admin_panel'`

- [ ] **Step 3: Modify `telegram_interface/_plugin_scanner.py`**

Add this import near the top, alongside the existing imports:

```python
from admin_panel.server import start as _start_admin_panel
```

Change the last two lines of `post_init_hook`'s inner `_post_init` function from:

```python
        asyncio.create_task(_loop(registry))
    return _post_init
```

to:

```python
        asyncio.create_task(_loop(registry))
        asyncio.create_task(_start_admin_panel(registry))
    return _post_init
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_plugin_scanner_admin_wiring.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full test suite to confirm nothing else regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, no failures (includes all tests added in Tasks 1-9)

- [ ] **Step 6: Commit**

```bash
git add telegram_interface/_plugin_scanner.py tests/test_plugin_scanner_admin_wiring.py
git commit -m "feat(admin-panel): start the admin server from the bot's post_init hook"
```

---

### Task 10: Manual smoke test against the real bot

This step is not unit-testable — it verifies real port binding and real process signal behavior, which Task 8's `_serve()` fix (Step 3) specifically targets.

**Files:** none (verification only).

- [ ] **Step 1: Start the bot for real**

Run: `bash run.sh` (or however the bot is normally started in this environment), and confirm the log shows the bot starting up without an admin-panel WARNING (i.e. `admin panel server failed to start` did NOT appear).

- [ ] **Step 2: Curl each endpoint from another terminal**

```bash
curl -s http://127.0.0.1:8765/api/metrics
curl -s http://127.0.0.1:8765/api/logs
curl -s http://127.0.0.1:8765/api/memory/facts
curl -s "http://127.0.0.1:8765/api/conversations"
```

Expected: each returns a JSON object (`{"error": ...}` is an acceptable response for a tier that isn't running locally, e.g. Letta — it should NOT be a connection-refused or 500).

- [ ] **Step 2b: Confirm loopback-only binding**

```bash
curl -s --max-time 3 http://0.0.0.0:8765/api/metrics; echo "exit=$?"
```

From a *different* machine on the same network (or via `ss -tlnp | grep 8765` locally), confirm the listening socket is bound to `127.0.0.1` only, not `0.0.0.0` — `ss -tlnp | grep 8765` should show `127.0.0.1:8765`, not `0.0.0.0:8765` or `*:8765`.

- [ ] **Step 3: Verify Telegram still works**

Send the bot a message on Telegram and confirm it replies normally — proves the admin server didn't block the polling loop.

- [ ] **Step 4: Verify clean shutdown**

Press Ctrl+C in the terminal running the bot. Expected: the process exits cleanly (no hang, no traceback about unhandled signal handlers) — this is the specific behavior the `_serve()` vs `serve()` choice in Task 8 was designed to protect.

- [ ] **Step 5: Report result to the user**

No commit for this task — report the smoke-test outcome back before considering the backend plan complete.

---

## Self-Review Notes

- **Spec coverage:** metrics (Task 4), logs (Task 5), L1/L2/L3 memory browsing (Task 6), conversations list + timeline (Task 7), same-process embedding via post_init (Tasks 8-9), config (Task 1), error-degradation contract (Tasks 6-7 tests), shared log-tail de-duplication (Task 2), new `list_chat_ids` (Task 3) — all design doc sections have a corresponding task.
- **Placeholder scan:** no TBD/TODO; every step has literal, runnable code.
- **Type consistency checked:** `tail_log(path, minutes, level, limit, max_lines) -> list[str]` signature is identical across Task 2's own tests, Task 5's route, and Task 2's tool refactor. `create_app(registry) -> FastAPI` and `start(registry) -> None` names match between Task 8's implementation and Task 9's import (`from admin_panel.server import start as _start_admin_panel`). `EpisodicMemory.list_chat_ids()` name matches between Task 3's implementation and Task 7's route usage.
- **Out of scope, confirmed absent from this plan:** no write/delete/promote routes, no authentication, no frontend files, no CORS setup (deferred to the frontend plan, which will also add the `dist/` static mount to `admin_panel/server.py`).
