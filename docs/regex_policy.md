# GOAT 2.0 — Regex Policy

**Status:** Standard (BUG-029)
**Date:** 2026-06-19

## Policy

The codebase follows a **"no regex in user-facing code"** policy:

- ✅ **Allowed** in `supervisor/`, `agents/`, `tools/`, `memory/`
  where a regex is genuinely required (URL parsing, email
  validation, time parsing). When used, the regex must be a
  pre-compiled constant at module level (not constructed per
  call), and the pattern must be documented.
- ✅ **Allowed** in `memory/temporal/time_parser.py` (ISO 8601)
  and `memory/working/working_search.py` (search-key parsing) —
  these are inherently regex-shaped.
- ❌ **Discouraged** for simple substring checks, whole-token
  matches, or balanced-brace extraction — use
  `utils.text_match` helpers instead:
  - `substring_match` instead of `re.search(re.escape(needle), text)`
  - `token_match` instead of `re.search(r"\b" + word + r"\b", text)`
  - `balanced_extract` for brace-balanced extraction
  - `extract_quoted_field` for `"key": "value"` extraction
  - `find_all_substrings` instead of `re.finditer(re.escape(needle), text)`

## Rationale

1. **Determinism** — pure substring/token matching is easier
   to reason about than regex behaviour on edge cases
   (greedy vs non-greedy, Unicode, multiline flags).
2. **Performance** — `str.find` is faster than `re.search` for
   short patterns (the regex engine has setup overhead).
3. **Safety** — a model-influenced string cannot inject regex
   metacharacters into a literal substring search.
4. **Auditability** — non-regex code is easier to grep, review,
   and refactor.

## Helper API

See `utils/text_match.py`:

```python
from utils.text_match import (
    substring_match, token_match, prefix_match,
    balanced_extract, extract_quoted_field,
    find_all_substrings,
)
```

## Audit status

| Module | Status |
|--------|--------|
| `supervisor/mechanisms/antirepeat.py` | OK no regex |
| `supervisor/mechanisms/staleness.py` | OK no regex |
| `supervisor/mechanisms/freshness.py` | OK no regex |
| `supervisor/mechanisms/context_builder.py` | OK no regex |
| `supervisor/mechanisms/corrections.py` | OK no regex |
| `supervisor/mechanisms/hints.py` | OK no regex |
| `supervisor/mechanisms/namespace.py` | OK no regex |
| `supervisor/pipeline/goat_call.py` | OK no regex |
| `supervisor/pipeline/prompt_helpers.py` | OK no regex (was regex — fixed in BUG-005) |
| `supervisor/pipeline/plan_validator.py` | OK no regex |
| `supervisor/pipeline/goat_enrichment.py` | OK no regex |
| `supervisor/session/turn_persistence.py` | OK no regex |
| `supervisor/session/mem_inject.py` | OK no regex |
| `supervisor/session/history.py` | OK no regex |
| `supervisor/classification/intent_complexity.py` | OK no regex |
| `supervisor/classification/intent_clarity.py` | OK no regex |
| `supervisor/classification/lang_detect.py` | (unchanged — not audited) |
| `supervisor/turn_runner.py` | OK no regex |
| `supervisor/background_drain.py` | OK no regex |
| `supervisor/errors_fallback.py` | OK no regex |
| `supervisor/behavior/analyzer.py` | OK no regex |
| `supervisor/behavior/style_learner.py` | OK no regex |
| `supervisor/behavior/store.py` | OK no regex |
| `supervisor/behavior/mirror.py` | OK no regex |
| `supervisor/behavior/profile.py` | OK no regex |
| `memory/temporal/temporal_format.py` | OK no regex |
| `memory/temporal/temporal_filter.py` | OK no regex |
| `memory/temporal/temporal_list.py` | OK no regex |
| `memory/temporal/temporal_search.py` | OK no regex |
| `memory/temporal/letta_routing_helpers.py` | OK no regex |
| `memory/memory_tools/memory_helpers.py` | OK no regex |
| `memory/memory_tools/memory_temporal_tools.py` | OK no regex |
| `agents/planner_decompose.py` | OK no regex |
| `agents/memory_agent.py` | OK no regex |
| `agents/coder.py` | TODO: uses re — needs audit |
| `agents/critique.py` | TODO: uses re — needs audit |
| `agents/researcher.py` | (unchanged — not audited) |
| `agents/critic.py` | (unchanged — not audited) |
| `agents/summarizer.py` | (unchanged — not audited) |
| `agents/tool_caller.py` | (unchanged — not audited) |
| `memory/shared/validation.py` | TODO: re for key validation, kept |
| `memory/working/working_search.py` | TODO: re for search key parsing, kept |
| `memory/temporal/time_parser.py` | TODO: re for ISO 8601, kept |
| `memory/episodic/chroma_helpers.py` | TODO: needs audit |
| `memory/memory_tools/memory_direct_query.py` | TODO: needs audit |
| `memory/router/classifier.py` | TODO: needs audit |
| `tools/dag/validators.py` | TODO: needs audit |
| `tools/system/shell_tool.py` | OK no regex (already non-regex) |

## See also

- `docs/superpowers/specs/2026-06-19-goat2-audit-report.md` — full audit
- `supervisor/pipeline/prompt_helpers.py:tool_schema_failure_hint` —
  reference implementation of non-regex brace extraction
- `utils/text_match.py` — the helper module
