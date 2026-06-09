# GOAT Onboarding System (Phase 5)

## Overview

The onboarding system provides a smooth first-session experience for new users.
It detects first-time users via a persistent flag in working memory (Redis) and
delivers a welcome message + adaptive hints over the first 4 turns.

**Zero architecture changes.** No new dependencies. No DAG modifications.
No agent modifications. No memory tier changes.

---

## How It Works

### Detection

`check_onboarding_done(mm)` in `supervisor/identity.py`:

1. Looks up key `onboarding_done` in working memory (Redis) via `mm.get()`
2. Returns `True` if the value is `"true"` — onboarding already completed
3. Returns `False` if missing or falsy — first session, show onboarding
4. Returns `True` if `mm` is `None` (safe fallback — assume done)

### Flow

```
User sends first message
  → supervisor.py computes turn_count = len(self._history.messages)
  → calls check_onboarding_done(self.memory_manager)
  → passes turn + onboarding_done to conv_result()
  → conv_result() passes them to direct_response()
  → direct_response() appends welcome/hint to system message
```

### Turn-based Content

| Turn | `onboarding_done` | Content |
|------|-------------------|---------|
| 1    | `False`           | Welcome message (box with GOAT capabilities summary) |
| 2    | `False`           | Hint: "I can read any file in the workspace" |
| 3    | `False`           | Hint: "I search the web in real time" |
| 4    | `False`           | Hint: "I can write code, analyze, compare" |
| 5+   | `False`           | No hints (normal operation) |
| any  | `True`            | No hints, no welcome |

### Persistence

After turn 4, `set_onboarding_done(mm)` writes `onboarding_done = "true"` to
working memory (Redis). This flag persists across sessions — the user will
never see the welcome message or hints again.

---

## Files Modified

### `supervisor/supervisor.py`

**Before** the `conv_result()` call in the CONVERSATIONAL branch:

```python
turn_count = len(self._history.messages)
onboarding_done = await check_onboarding_done(self.memory_manager)
```

These two values are passed as keyword arguments to `conv_result()`:

```python
r = await conv_result(
    ...,
    turn=turn_count,
    onboarding_done=onboarding_done,
)
```

### `supervisor/identity.py`

No modifications needed — the onboarding logic was already implemented:

- `check_onboarding_done()` — reads flag from working memory
- `set_onboarding_done()` — writes flag to working memory
- `_build_welcome_message()` — returns welcome string at turn 1
- `_build_adaptive_hint()` — returns hint at turns 2-4
- `direct_response()` — appends onboarding content to system message
- `conv_result()` — passes `turn` and `onboarding_done` to `direct_response()`

---

## Configuration

No configuration needed. All constants are in `supervisor/identity.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `_ONBOARDING_KEY` | `"onboarding_done"` | Redis key for the flag |
| `_WELCOME_MESSAGE` | Box with GOAT logo + capabilities | Shown at turn 1 |
| `_HINTS` | 3 strings | Rotating hints for turns 2-4 |

To customize the welcome message or hints, edit these constants in
`supervisor/identity.py`. No restart needed — changes take effect on next turn.

---

## Quick Start

### One-Click Launch (No Terminal Required)

Two launcher scripts are provided for instant startup:

| File | OS | Action |
|------|----|--------|
| `goat.bat` | Windows | **Double-click** → auto-checks Python + deps → runs `main.py` |
| `goat.sh` | Linux / macOS | `chmod +x goat.sh && ./goat.sh` → same flow |

### `goat.bat` (Windows)

```batch
@echo off
title GOAT - Onboarding
echo ============================================
echo  Welcome to GOAT - Multi-Agent Supervisor
echo ============================================
echo.
echo Checking Python installation...
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo Checking dependencies...
if not exist "requirements.txt" (
    echo [WARN] requirements.txt not found. Skipping dependency check.
) else (
    pip install -r requirements.txt >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo [WARN] Some dependencies may be missing. Attempting to continue...
    ) else (
        echo Dependencies OK.
    )
)

echo.
echo Starting GOAT...
echo.
python main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] GOAT exited with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

pause
```

### `goat.sh` (Linux / macOS)

```bash
#!/usr/bin/env bash
set -e

echo "============================================"
echo " Welcome to GOAT - Multi-Agent Supervisor"
echo "============================================"
echo ""

# Detect Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python is not installed or not in PATH."
    echo "Please install Python 3.10+ from https://python.org"
    exit 1
fi

echo "Using: $($PYTHON --version)"

# Check dependencies
if [ ! -f "requirements.txt" ]; then
    echo "[WARN] requirements.txt not found. Skipping dependency check."
else
    echo "Checking dependencies..."
    $PYTHON -m pip install -r requirements.txt -q 2>/dev/null && echo "Dependencies OK." || echo "[WARN] Some deps may be missing."
fi

echo ""
echo "Starting GOAT..."
echo ""
$PYTHON main.py
```

### What the Launchers Do

1. **Check Python** — verifies Python 3.10+ is installed and in PATH
2. **Check dependencies** — auto-installs from `requirements.txt` if present
3. **Launch GOAT** — runs `main.py` with the same behavior as manual terminal start

No registration. No installer. No config files to edit.

---

## Requirements

**None.** The onboarding system uses only:

- `logging` — standard library
- `datetime` — standard library
- `os` — standard library
- `MemoryManager.get()` / `MemoryManager.store()` — existing GOAT interfaces

No pip install, no new dependencies, no external services.

---

## Testing

### Manual Test

1. Clear working memory (Redis): `redis-cli FLUSHDB`
2. Send a message to GOAT
3. Verify welcome message appears
4. Send 3 more messages — verify hints appear
5. Send a 5th message — verify no hints
6. Start a new session — verify no welcome/hints

### Automated Test (if applicable)

```python
from supervisor.identity import check_onboarding_done, set_onboarding_done

async def test_onboarding(mm):
    # Fresh state
    assert await check_onboarding_done(mm) == False

    # After setting
    await set_onboarding_done(mm)
    assert await check_onboarding_done(mm) == True
```

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Redis down | `check_onboarding_done()` returns `True` (assume done) |
| `mm` is `None` | `check_onboarding_done()` returns `True` (safe fallback) |
| `set_onboarding_done()` fails | Logged as warning, non-critical |
| Turn count > 4 | No hints, normal operation |
| Multiple sessions | Flag persists in Redis — no repeated welcome |

---

## Architecture Note

The onboarding system respects the GOAT architecture:

- **GOAT (supervisor):** reads memory, computes turn count, orchestrates
- **DAG agents:** not involved in onboarding at all
- **Memory:** working tier (Redis) only — no ChromaDB or Letta writes for onboarding

The onboarding content is appended to the **system message**, not injected as
a separate user/assistant message. This keeps the conversation history clean
and prevents the LLM from treating the welcome as part of the dialogue.
