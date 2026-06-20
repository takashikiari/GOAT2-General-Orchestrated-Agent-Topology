# GOAT 2.0 — Docstring Policy

**Status:** Standard (BUG-031)
**Date:** 2026-06-19

## Policy

Every public function, method, and class in GOAT 2.0 must
have a docstring that accurately describes its behaviour.
Specifically:

1. **No false claims.**
   - Don't claim "no hardcoded numbers" when the module
     carries `_DEFAULTS` for defensive fallback.
   - Don't claim "never raises" on a pure helper that
     propagates exceptions normally.
   - Don't claim "best-effort; never raises" on a function
     that has a path where an exception could escape
     (e.g. an unguarded `await` outside the try block).

2. **Distinguish "expected" from "unexpected" failures.**
   A docstring should say what happens when an exception is
   caught and what happens when one is not. If the function
   falls back to a default on failure, the docstring must
   describe the default. If the function propagates the
   exception, the docstring must say so.

3. **Match the actual log level.**
   If a docstring says "logs at WARNING", the code must
   call `log.warning(...)`. If it says "logs at DEBUG",
   the code must call `log.debug(...)`. Tests in
   `tests/test_logging_policy.py` pin this down.

4. **Honest about side effects.**
   A function that mutates a global cache must say so. A
   function that spawns a background task must say so. A
   function that calls LLM APIs must say so.

## Why

Honest docstrings are the contract between the implementer
and the reviewer. A docstring that lies is worse than no
docstring — it actively misleads the next person who has
to debug, refactor, or extend the code.

## Verification

The test `tests/test_honest_docstrings.py` pins down the
specific lies the audit found and prevents regression:

  - `freshness.py` docstring no longer claims "no hardcoded
    numbers" (it carries a `_DEFAULTS` defensive fallback).
  - `utils.text_match` helpers do NOT claim "never raises"
    in their docstrings.
  - `style_sync.refresh_style`, `turn_persistence.store_and_promote`,
    and `behavior.store.load_style` are documented as
    best-effort AND verified to not raise in practice.

## See also

- `docs/regex_policy.md` — the regex analogue of this audit
- `docs/logging_policy.md` — log-level conventions
- `tests/test_honest_docstrings.py` — regression tests
