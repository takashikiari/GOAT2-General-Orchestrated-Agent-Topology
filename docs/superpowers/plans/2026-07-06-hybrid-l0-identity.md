# Hybrid L0 Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store the L0 identity prompt as an overridable `identity` block in Letta, so GOAT can update its own persona at runtime via a `set_identity` tool, while keeping the config `base_prompt` as a guaranteed fallback when Letta is unreachable.

**Architecture:** `PermanentMemory` gains two methods that read/write a Letta core-memory block named `identity` (separate from `facts`). `MemoryLayers.get_identity_prompt()` tries the override first and falls back to `IDENTITY_BASE_PROMPT` from config on any error. `assemble_context()` accepts an `identity_prompt` pre-fetch parameter so the orchestrator can fetch it concurrently with L1/L2. A new `set_identity` tool lets GOAT update or clear the override in the same turn. Letta down = config fallback, identical to today.

**Tech Stack:** Python 3.11+, httpx (Letta HTTP API), pytest with `unittest.mock.AsyncMock` (no real Letta in tests).

## Global Constraints

- Max 90 lines per new file; single responsibility.
- No new LLM calls.
- Letta down must never crash a turn — every Letta call in this feature degrades gracefully.
- Backward-compatible: existing agents without the `identity` block get `None` (404 → fallback to config). New agents get the block created at agent-creation time.
- `python3 -m pytest tests/ -v` must stay green after every task.
- No placeholder code, no TODO comments.

---

## File Map

| Action | Path | What changes |
|--------|------|-------------|
| Modify | `memory/permanent/permanent.py` | Add `_IDENTITY_LABEL`, `get_identity_override()`, `set_identity_override()`; add block to agent creation |
| Modify | `memory/layers.py` | Add `get_identity_prompt()`, `set_identity_override()`; add `identity_prompt` param to `assemble_context()` |
| Modify | `tests/_orch_fakes.py` | Add `get_identity_prompt()` to `_FakeLayers` |
| Create | `tools/identity_tool.py` | `build_set_identity_tool(memory_layers)` |
| Modify | `orchestrator/orchestrator.py` | Add `identity_task` concurrent with `facts_task`; pass `identity_prompt` to `assemble_context` |
| Modify | `telegram_interface/bot.py` | Import and register `set_identity` tool |
| Create | `tests/test_permanent_identity.py` | Unit tests for identity get/set (mocked HTTP) |
| Create | `tests/test_identity_layers.py` | Unit tests for `get_identity_prompt` and `set_identity_override` |

---

### Task 1: `PermanentMemory` — identity block get/set

**Files:**
- Modify: `memory/permanent/permanent.py`
- Create: `tests/test_permanent_identity.py`

**Interfaces:**
- Produces:
  - `PermanentMemory.get_identity_override() -> str | None` — returns stored override or `None`
  - `PermanentMemory.set_identity_override(text: str) -> None` — PATCH block, POST if 404 (creates it)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permanent_identity.py`:

```python
"""Tests for PermanentMemory identity block (mocked HTTP, no real Letta)."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from memory.permanent.permanent import PermanentMemory


def _make_pm(agent_id="agent-1") -> tuple[PermanentMemory, MagicMock]:
    """Return a PermanentMemory with pre-set agent_id and a mock HTTP client."""
    pm = PermanentMemory()
    pm._agent_id = agent_id
    http = AsyncMock()
    pm._http = http
    return pm, http


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


@pytest.mark.asyncio
async def test_get_identity_override_returns_value():
    pm, http = _make_pm()
    http.get.return_value = _resp(200, {"value": "You are a pirate."})
    assert await pm.get_identity_override() == "You are a pirate."


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_404():
    pm, http = _make_pm()
    http.get.return_value = _resp(404)
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_empty():
    pm, http = _make_pm()
    http.get.return_value = _resp(200, {"value": "   "})
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_get_identity_override_returns_none_on_exception():
    pm, http = _make_pm()
    http.get.side_effect = Exception("network error")
    assert await pm.get_identity_override() is None


@pytest.mark.asyncio
async def test_set_identity_override_patches_existing_block():
    pm, http = _make_pm()
    http.patch.return_value = _resp(200)
    await pm.set_identity_override("You are a helpful assistant named Max.")
    http.patch.assert_called_once()
    call_kwargs = http.patch.call_args
    assert "identity" in str(call_kwargs)
    assert "You are a helpful assistant named Max." in str(call_kwargs)


@pytest.mark.asyncio
async def test_set_identity_override_creates_block_on_404():
    pm, http = _make_pm()
    http.patch.return_value = _resp(404)
    http.post.return_value = _resp(200)
    await pm.set_identity_override("New identity.")
    http.post.assert_called_once()
    call_kwargs = http.post.call_args
    assert "identity" in str(call_kwargs)
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd /home/lenovo/workspace/goat2
python3 -m pytest tests/test_permanent_identity.py -v 2>&1 | tail -15
```

Expected: `AttributeError` — `get_identity_override` not defined yet.

- [ ] **Step 3: Implement `get_identity_override` and `set_identity_override`**

In `memory/permanent/permanent.py`, add `_IDENTITY_LABEL = "identity"` after `_FACTS_LABEL`:

```python
_IDENTITY_LABEL = "identity"
```

Update agent creation in `_resolve_agent_id` — change `memory_blocks` to include both blocks:

```python
            r = await http.post("/v1/agents/", json={
                "name": PERMANENT_AGENT_NAME,
                "model": PERMANENT_LETTA_MODEL,
                "memory_blocks": [
                    {"label": _FACTS_LABEL, "value": "{}"},
                    {"label": _IDENTITY_LABEL, "value": ""},
                ],
            })
```

Append after `delete_fact`:

```python
    async def get_identity_override(self) -> str | None:
        """Return the Letta identity override, or None if unset / unavailable.

        Never raises — a 404 (block absent on old agents) or any network error
        returns None, which signals the caller to fall back to the config prompt.
        """
        try:
            agent_id = await self._resolve_agent_id()
            resp = await self._get_http().get(
                f"/v1/agents/{agent_id}/core-memory/blocks/{_IDENTITY_LABEL}"
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            value = resp.json().get("value", "").strip()
            return value or None
        except Exception:  # noqa: BLE001 — identity is best-effort
            return None

    async def set_identity_override(self, text: str) -> None:
        """Write (or create) the identity block in Letta.

        Tries PATCH first; if the block doesn't exist on an older agent (404),
        falls back to POST to create it. Raises on any other HTTP error.
        """
        agent_id = await self._resolve_agent_id()
        http = self._get_http()
        resp = await http.patch(
            f"/v1/agents/{agent_id}/core-memory/blocks/{_IDENTITY_LABEL}",
            json={"value": text},
        )
        if resp.status_code == 404:
            resp = await http.post(
                f"/v1/agents/{agent_id}/core-memory/blocks",
                json={"label": _IDENTITY_LABEL, "value": text},
            )
        resp.raise_for_status()
        log.debug("PermanentMemory: identity override set (%d chars)", len(text))
```

- [ ] **Step 4: Run tests — expect green**

```bash
python3 -m pytest tests/test_permanent_identity.py -v 2>&1 | tail -15
```

Expected: all 6 pass.

- [ ] **Step 5: Run full suite — expect green**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass (136 tests).

- [ ] **Step 6: Commit**

```bash
git add memory/permanent/permanent.py tests/test_permanent_identity.py
git commit -m "feat: add identity block get/set to PermanentMemory"
```

---

### Task 2: `MemoryLayers` plumbing + `_FakeLayers` update

**Files:**
- Modify: `memory/layers.py`
- Modify: `tests/_orch_fakes.py`
- Create: `tests/test_identity_layers.py`

**Interfaces:**
- Consumes: `PermanentMemory.get_identity_override()`, `PermanentMemory.set_identity_override()` (Task 1)
- Produces:
  - `MemoryLayers.get_identity_prompt() -> str`
  - `MemoryLayers.set_identity_override(text: str) -> None`
  - `MemoryLayers.assemble_context(..., identity_prompt: str | None = None)` — uses override when provided, else `_BASE_IDENTITY`
  - `_FakeLayers.get_identity_prompt() -> str` — returns `IDENTITY_BASE_PROMPT`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity_layers.py`:

```python
"""Tests for MemoryLayers identity prompt plumbing (no real backends)."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from memory.config import IDENTITY_BASE_PROMPT


class _FakePermanent:
    def __init__(self, override=None, raise_on_set=False):
        self._override = override
        self._raise_on_set = raise_on_set
        self.set_calls: list[str] = []

    async def get_identity_override(self):
        return self._override

    async def set_identity_override(self, text):
        if self._raise_on_set:
            raise Exception("Letta down")
        self.set_calls.append(text)

    async def get_all_facts(self):
        return {}


def _make_layers(override=None, raise_on_set=False):
    from memory.layers import MemoryLayers
    layers = MemoryLayers.__new__(MemoryLayers)
    layers._permanent = _FakePermanent(override=override, raise_on_set=raise_on_set)
    layers._working = AsyncMock()
    layers._working.get_messages.return_value = []
    layers._episodic = AsyncMock()
    layers._cache = AsyncMock()
    layers._activation = AsyncMock()
    return layers


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_override_when_set():
    layers = _make_layers(override="You are a pirate assistant.")
    result = await layers.get_identity_prompt()
    assert result == "You are a pirate assistant."


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_base_when_no_override():
    layers = _make_layers(override=None)
    result = await layers.get_identity_prompt()
    assert result == IDENTITY_BASE_PROMPT


@pytest.mark.asyncio
async def test_get_identity_prompt_returns_base_on_exception():
    layers = _make_layers()
    layers._permanent.get_identity_override = AsyncMock(side_effect=Exception("fail"))
    result = await layers.get_identity_prompt()
    assert result == IDENTITY_BASE_PROMPT


@pytest.mark.asyncio
async def test_set_identity_override_delegates_to_permanent():
    layers = _make_layers()
    await layers.set_identity_override("New persona.")
    assert layers._permanent.set_calls == ["New persona."]


@pytest.mark.asyncio
async def test_assemble_context_uses_provided_identity_prompt():
    layers = _make_layers()
    layers._working.get_messages.return_value = []
    blocks, _ = await layers.assemble_context(
        "chat1", identity_prompt="Custom prompt here."
    )
    assert any("Custom prompt here." in b for b in blocks)


@pytest.mark.asyncio
async def test_assemble_context_falls_back_to_base_when_none():
    layers = _make_layers()
    layers._working.get_messages.return_value = []
    blocks, _ = await layers.assemble_context("chat1", identity_prompt=None)
    assert any(IDENTITY_BASE_PROMPT in b for b in blocks)
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_identity_layers.py -v 2>&1 | tail -15
```

Expected: `AttributeError` — `get_identity_prompt` not defined yet.

- [ ] **Step 3: Add `get_identity_prompt` and `set_identity_override` to `MemoryLayers`**

In `memory/layers.py`, after `get_identity_and_facts()` (around line 91), add:

```python
    async def get_identity_prompt(self) -> str:
        """L0: return Letta identity override if set, else fall back to config base_prompt.

        Never raises — any Letta failure returns the config prompt so every turn
        has a guaranteed identity even when the permanent tier is unreachable.
        """
        try:
            override = await self._permanent.get_identity_override()
            if override:
                return override
        except Exception:  # noqa: BLE001 — L0 is always guaranteed
            pass
        return _BASE_IDENTITY

    async def set_identity_override(self, text: str) -> None:
        """Write a new L0 identity override to Letta.

        Passing an empty string clears the override so the config prompt
        is used again. Raises if Letta is unavailable.
        """
        await self._permanent.set_identity_override(text)
```

- [ ] **Step 4: Add `identity_prompt` param to `assemble_context`**

In `memory/layers.py`, find the `assemble_context` signature:

```python
    async def assemble_context(
        self, chat_id: str, budget: int | None = None,
        l3_results: list[dict] | None = None,
        facts: dict[str, str] | None = None,
        messages: list[dict] | None = None,
    ) -> tuple[list[str], int]:
```

Replace with:

```python
    async def assemble_context(
        self, chat_id: str, budget: int | None = None,
        l3_results: list[dict] | None = None,
        facts: dict[str, str] | None = None,
        messages: list[dict] | None = None,
        identity_prompt: str | None = None,
    ) -> tuple[list[str], int]:
```

Find the line that builds the identity block (around line 344):

```python
        identity = f"[Identity]\n{_BASE_IDENTITY}\nCurrent time: {now}"
```

Replace with:

```python
        base = identity_prompt if identity_prompt is not None else _BASE_IDENTITY
        identity = f"[Identity]\n{base}\nCurrent time: {now}"
```

- [ ] **Step 5: Add `get_identity_prompt` to `_FakeLayers`**

In `tests/_orch_fakes.py`, after the `get_identity_and_facts` method, add:

```python
    async def get_identity_prompt(self):
        from memory.config import IDENTITY_BASE_PROMPT
        return IDENTITY_BASE_PROMPT
```

- [ ] **Step 6: Run tests — expect green**

```bash
python3 -m pytest tests/test_identity_layers.py tests/test_permanent_identity.py -v 2>&1 | tail -20
```

Expected: all 12 pass.

- [ ] **Step 7: Run full suite — expect green**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass (136+ tests).

- [ ] **Step 8: Commit**

```bash
git add memory/layers.py tests/_orch_fakes.py tests/test_identity_layers.py
git commit -m "feat: thread identity_prompt through MemoryLayers and assemble_context"
```

---

### Task 3: Tool + orchestrator wiring + bot registration

**Files:**
- Create: `tools/identity_tool.py`
- Modify: `orchestrator/orchestrator.py`
- Modify: `telegram_interface/bot.py`
- Modify: `tests/test_orchestrator_memory_flow.py` (append 1 test)

**Interfaces:**
- Consumes:
  - `layers.get_identity_prompt() -> str` (Task 2)
  - `layers.set_identity_override(text: str) -> None` (Task 2)
  - `layers.assemble_context(..., identity_prompt=str)` (Task 2)
- Produces: `set_identity` tool available to GOAT; identity fetched concurrently each turn.

- [ ] **Step 1: Read existing tool patterns**

```bash
wc -l /home/lenovo/workspace/goat2/tools/memory_promote.py
head -30 /home/lenovo/workspace/goat2/tools/memory_promote.py
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_orchestrator_memory_flow.py`:

```python
# --- identity_prompt flows through assemble_context --------------------------

def test_identity_prompt_is_fetched_and_used():
    """Orchestrator must call get_identity_prompt and pass it to assemble_context."""
    identity_used = []

    class _IdentityCaptureLayers(_FakeLayers):
        async def get_identity_prompt(self):
            return "Custom identity for this test."

        async def assemble_context(self, chat_id, budget=None, l3_results=None,
                                   facts=None, messages=None, identity_prompt=None):
            identity_used.append(identity_prompt)
            return list(self._blocks), self._l3_used

    layers = _IdentityCaptureLayers()
    llm = _LLMClient(_Completions("ok"))
    orch = Orchestrator(layers, llm, _FakePluginManager(), _FakeAnalytics())
    asyncio.run(orch.run("hello", "chat1"))
    assert identity_used, "assemble_context was never called"
    assert identity_used[0] == "Custom identity for this test.", (
        f"expected custom identity, got {identity_used[0]!r}"
    )
```

- [ ] **Step 3: Run the new test — expect failure**

```bash
python3 -m pytest tests/test_orchestrator_memory_flow.py -v -k "identity_prompt" 2>&1 | tail -10
```

Expected: FAIL — `_FakeLayers` has no `get_identity_prompt` or orchestrator doesn't pass it.

- [ ] **Step 4: Create `tools/identity_tool.py`**

```python
"""tools.identity_tool — set_identity tool: GOAT's path to update L0 identity.

GOAT calls this when the user explicitly asks it to change its name, persona,
or behaviour at the identity level. The new prompt is stored in Letta and
overrides the config base_prompt on every future turn. Passing an empty string
clears the override and restores the config prompt.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from memory.layers import MemoryLayers

log = get_logger(__name__)

__all__ = ["build_set_identity_tool"]


def build_set_identity_tool(memory_layers: "MemoryLayers") -> ToolDefinition:
    """Build the set_identity tool, bound to a ``MemoryLayers`` instance."""

    async def handler(identity_prompt: str, chat_id: str = "") -> str:
        if identity_prompt.strip() == "":
            try:
                await memory_layers.set_identity_override("")
                log.info("set_identity: cleared override chat=%s", chat_id)
                return "✅ Identity reset to default (config base_prompt)."
            except Exception as exc:
                return f"❌ set_identity failed (Letta unavailable): {exc}"
        try:
            await memory_layers.set_identity_override(identity_prompt.strip())
            log.info("set_identity: updated chat=%s (%d chars)", chat_id, len(identity_prompt))
            return f"✅ Identity updated ({len(identity_prompt.strip())} chars). Takes effect next turn."
        except Exception as exc:
            return f"❌ set_identity failed (Letta unavailable): {exc}"

    return ToolDefinition(
        name="set_identity",
        description=(
            "Update GOAT's core identity prompt (L0) stored in permanent memory. "
            "Use this when the user explicitly asks you to change your name, persona, "
            "or fundamental behaviour — e.g. 'from now on call yourself Max' or "
            "'always respond in Romanian'. The new prompt overrides the config default "
            "and persists across all future sessions. Pass an empty string to reset "
            "to the default config prompt."
        ),
        parameters={
            "type": "object",
            "properties": {
                "identity_prompt": {
                    "type": "string",
                    "description": (
                        "The full identity prompt to use from now on. "
                        "Pass an empty string to reset to the default."
                    ),
                },
            },
            "required": ["identity_prompt"],
        },
        handler=handler,
    )
```

- [ ] **Step 5: Update `orchestrator/orchestrator.py` — concurrent identity fetch**

Find the block starting at line 262 where tasks are created:

```python
            facts_task = asyncio.create_task(layers.get_identity_and_facts())
            msgs_task = asyncio.create_task(layers.get_working_context(chat_id))
```

Replace with:

```python
            facts_task = asyncio.create_task(layers.get_identity_and_facts())
            identity_task = asyncio.create_task(layers.get_identity_prompt())
            msgs_task = asyncio.create_task(layers.get_working_context(chat_id))
```

Find the assemble block around line 334:

```python
            facts = await facts_task
            messages = await msgs_task
            context_blocks, l3_used = await layers.assemble_context(
                chat_id, budget=budget, l3_results=l3_results,
                facts=facts, messages=messages,
            )
```

Replace with:

```python
            facts = await facts_task
            identity_prompt = await identity_task
            messages = await msgs_task
            context_blocks, l3_used = await layers.assemble_context(
                chat_id, budget=budget, l3_results=l3_results,
                facts=facts, messages=messages,
                identity_prompt=identity_prompt,
            )
```

- [ ] **Step 6: Update `telegram_interface/bot.py` — register the tool**

Find the imports block around line 32:

```python
from tools.memory_promote import build_promote_memory_tool
from tools.memory_tools import build_search_memory_tool
from tools.memory_writer import build_store_memory_tool
```

Add:

```python
from tools.identity_tool import build_set_identity_tool
```

Find where tools are built around line 157:

```python
    search_memory = build_search_memory_tool(layers)
    store_memory = build_store_memory_tool(layers)
    promote_memory = build_promote_memory_tool(layers)
    manager_tools = build_memory_manager_tools(layers)
```

Add:

```python
    set_identity = build_set_identity_tool(layers)
```

Find the Orchestrator constructor around line 219:

```python
        tools=[search_memory, store_memory, promote_memory, *manager_tools, *workflow_tools],
```

Replace with:

```python
        tools=[search_memory, store_memory, promote_memory, set_identity, *manager_tools, *workflow_tools],
```

- [ ] **Step 7: Run the new test — expect green**

```bash
python3 -m pytest tests/test_orchestrator_memory_flow.py -v -k "identity_prompt" 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 8: Run full suite — expect green**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all pass (142+ tests).

- [ ] **Step 9: Commit**

```bash
git add tools/identity_tool.py orchestrator/orchestrator.py telegram_interface/bot.py tests/test_orchestrator_memory_flow.py
git commit -m "feat: set_identity tool + orchestrator concurrent identity fetch"
```

- [ ] **Step 10: Push**

```bash
git push
```

---

## Self-Review

**Spec coverage:**
- ✅ Identity override stored in Letta `identity` block (separate from `facts`)
- ✅ `get_identity_prompt()` tries Letta override, falls back to config on any failure
- ✅ `assemble_context()` accepts pre-fetched `identity_prompt` — fetched concurrently
- ✅ `set_identity` tool: GOAT can update or clear the override in the same turn
- ✅ Letta down → config fallback (no crash, L0 still injected every turn)
- ✅ Old agents without the `identity` block → 404 → `None` → config fallback
- ✅ New agents get `identity` block created at agent-creation time (empty = no override)
- ✅ Empty string clears the override (reset to config)
- ✅ No new LLM calls

**Placeholder scan:** None.

**Type consistency:**
- `get_identity_override() -> str | None` (PermanentMemory) → consumed by `get_identity_prompt() -> str` (MemoryLayers) — types consistent
- `identity_prompt: str | None = None` in `assemble_context` → `identity_prompt = await identity_task` in orchestrator (always `str`) — consistent
- `set_identity_override(text: str)` — same signature in PermanentMemory and MemoryLayers — consistent
