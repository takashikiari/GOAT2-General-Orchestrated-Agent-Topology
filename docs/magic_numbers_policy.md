# GOAT 2.0 — Magic Numbers Policy

**Status:** Standard (BUG-032)
**Date:** 2026-06-19

## Policy

Cross-module magic numbers (timeouts, caps, thresholds) are
centralised in `config/limits.py`. Module-local defaults
(e.g. `_DEFAULT_TEMPLATE` in `errors_fallback.py`) stay
where they are — they're genuinely module-private.

## When to add to `config/limits.py`

A constant belongs in `config/limits.py` when **all three**
are true:

1. **Cross-module**: referenced from more than one module.
2. **Operator-tunable**: would benefit from being adjustable
   via config without a code change.
3. **Important**: an operator would want a single source of
   truth for documentation and tuning.

If only one or two of those hold, keep the constant local.

## When to keep module-local

  - Format strings (e.g. `_DEFAULT_TEMPLATE`)
  - Internal thresholds that only the local function needs
    (e.g. `_WEIGHTS` for the complexity scorer — fine to keep
    in `intent_complexity.py`).
  - Library-internal defaults that aren't operationally
    meaningful (e.g. `__all__` lists, log namespaces).

## Audit status

| Constant | Canonical source | Module-local mirror |
|----------|------------------|---------------------|
| Conversation history cap | `config.limits.DEFAULT_HISTORY_MAX_MESSAGES` | `_DEFAULT_MAX_MESSAGES` in `session/history.py` |
| Prompt entries cap | `config.limits.DEFAULT_PROMPT_MAX_ENTRIES` | `_DEFAULT_MAX_ENTRIES` in `mechanisms/context_builder.py` |
| Background drain timeout | `config.limits.DEFAULT_BACKGROUND_DRAIN_TIMEOUT_S` | `DEFAULT_DRAIN_TIMEOUT_S` in `background_drain.py` |
| Error fallback max chars | `config.limits.DEFAULT_ERROR_MAX_CHARS` | `_DEFAULT_MAX_CHARS` in `errors_fallback.py` |
| Corrections recall limit | `config.limits.DEFAULT_CORRECTIONS_LIMIT` | `DEFAULT_LIMIT` in `mechanisms/corrections.py` |
| Temporal fresh threshold | `config.limits.DEFAULT_TEMPORAL_FRESH_THRESHOLD_S` | `DEFAULT_FRESH_THRESHOLD_S` in `temporal/temporal_format.py` |
| Temporal recent threshold | `config.limits.DEFAULT_TEMPORAL_RECENT_THRESHOLD_S` | `DEFAULT_RECENT_THRESHOLD_S` in `temporal/temporal_format.py` |
| Temporal day threshold | `config.limits.DEFAULT_TEMPORAL_DAY_THRESHOLD_S` | `DEFAULT_DAY_THRESHOLD_S` in `temporal/temporal_format.py` |
| SHELL timeout cap | (unchanged) | `_MAX_TIMEOUT_S = 60` in `tools/system/shell_tool.py` |
| Intent complexity thresholds | (unchanged) | `COMPLEXITY_THRESHOLDS` in `classification/intent_complexity.py` |
| Scorer weights | (unchanged) | `_WEIGHTS` in `classification/intent_complexity.py` |
| Tool result preview cap | (unchanged) | `max_preview_chars=500` in `prompt_helpers.normalise_empty_response_with_tools` |
| Prompt intent truncation | (unchanged) | `_MAX_INTENT_CHARS = 4_000` in `prompt_helpers.build_user_prompt` |
| Planner intent truncation | (unchanged) | `_MAX_INTENT_CHARS = 4_000` in `planner_decompose` |
| DAG intent keywords | (unchanged) | `DAG_INTENT_KEYWORDS` in `mechanisms/staleness.py` |
| Repetitive threshold | (unchanged) | `REPETITIVE_THRESHOLD` in `mechanisms/antirepeat.py` |

## See also

- `config/limits.py` — the central source
- `tests/test_config_limits.py` — regression tests that verify
  the module-local mirrors stay aligned with the canonical values