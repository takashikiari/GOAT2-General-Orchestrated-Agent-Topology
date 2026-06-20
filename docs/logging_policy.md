# GOAT 2.0 — Logging Policy

**Status:** Standard (BUG-030)
**Date:** 2026-06-19

## Policy

A caught exception must be logged at a level that reflects
**what the operator should do about it**, not what the
caller felt when writing the ``except`` block.

| Situation | Log level | Helper |
|-----------|-----------|--------|
| Expected failure (e.g. search returns no results, optional feature missing) | `log.debug` | direct |
| Recoverable failure (fallback path is in place; failure is unusual) | `log.warning` | `safe_log_exception` |
| Unexpected failure (no fallback; the caller kept the turn alive to avoid a crash) | `log.warning` + `exc_info=True` | `log_unexpected_failure` |
| Uncaught exception that crashes a turn | `log.exception` | direct |

## Rationale

1. **Visibility** — operators monitoring logs should see failures
   that need investigation. ``log.debug`` is filtered out in
   production, so any caught exception logged at DEBUG is
   effectively invisible. Recurring failures must be visible.
2. **Signal-to-noise** — expected failures (a memory search
   that returns zero hits, a tool that's temporarily
   unavailable) should NOT generate WARNING log lines. A
   constant stream of warnings for normal flow events makes
   it hard to spot the real problems.
3. **Reproducibility** — every WARNING log should include the
   exception class, the message, and ideally the stack trace
   so the operator can reproduce locally.

## Helper API

See `utils/logging_policy.py`:

```python
from utils.logging_policy import safe_log_exception, log_unexpected_failure

try:
    await do_thing()
except Exception as exc:
    # Operator-visible — failure was not part of normal flow.
    safe_log_exception(log, "do_thing failed", exc)
    fallback()
```

## Anti-patterns

❌ `log.debug("foo failed: %s", exc)` — invisible in production.

❌ `log.error("foo failed: %s", exc)` without `exc_info=True` —
   the stack trace is dropped, so the operator can't
   reproduce the failure.

❌ Bare `except: pass` — even DEBUG silence is worse than
   `log.debug` with the exception text.

## Audit status

| Module | Level for caught exceptions |
|--------|------------------------------|
| `supervisor/mechanisms/antirepeat.py` | mostly DEBUG (expected: history dedup is normal) |
| `supervisor/mechanisms/staleness.py` | no logging |
| `supervisor/mechanisms/freshness.py` | no logging |
| `supervisor/mechanisms/context_builder.py` | no logging |
| `supervisor/mechanisms/corrections.py` | DEBUG (expected: episodic search may fail) |
| `supervisor/mechanisms/hints.py` | DEBUG (expected) |
| `supervisor/pipeline/goat_call.py` | WARNING (operator-visible LLM failures) |
| `supervisor/pipeline/plan_validator.py` | no logging |
| `supervisor/session/turn_persistence.py` | WARNING (BUG-027 fix) |
| `supervisor/session/mem_inject.py` | DEBUG (expected) |
| `supervisor/turn_runner.py` | no logging (delegated) |
| `supervisor/errors_fallback.py` | WARNING (operator-visible) |
| `supervisor/background_drain.py` | WARNING (drain timeout) |
| `supervisor/behavior/analyzer.py` | DEBUG (config load) |
| `supervisor/behavior/style_learner.py` | DEBUG (config load) |
| `supervisor/behavior/store.py` | WARNING (Letta unreachable) |
| `memory/memory_tools/memory_helpers.py` | DEBUG (tool wrapper) |
| `memory/memory_tools/memory_temporal_tools.py` | DEBUG |
| `memory/memory_tools/letta_routing_helpers.py` | WARNING (Letta timeout) |
| `memory/temporal/temporal_filter.py` | DEBUG |
| `memory/temporal/temporal_format.py` | DEBUG |
| `agents/planner_decompose.py` | WARNING (plan validation) |
| `agents/memory_agent.py` | DEBUG |
| `tools/system/shell_tool.py` | WARNING (validation failures) |

## See also

- `utils/logging_policy.py` — `safe_log_exception`, `log_unexpected_failure`
