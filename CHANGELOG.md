# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-06 (patch 55)

### Fixed

#### Telegram token resolution — env var takes precedence over goat.toml

**`supervisor/interfaces/telegram_bot.py`**:
- `_TOKEN` now resolves via: `TELEGRAM_TOKEN` env var → `goat.toml` `[channels].telegram_token` → error.
- Uses `os.environ.get("TELEGRAM_TOKEN")` first, falls back to `load_toml().channel_str("telegram_token")`.
- `build_app()` raises `RuntimeError` with clear message if neither source provides a token.
- Module docstring updated to document the resolution order.
- This allows operators to keep `goat.toml` clean of secrets while using environment variables in production.

All 37 existing tests pass. No imports broken. File remains ≤200 lines with docstrings.

---

## [Unreleased] — 2026-06-06 (patch 54)

### Fixed

#### P1: Empty output after file tool call (tool_runner.py — already applied, confirmed)
`_call_with_tools` no-tool-calls return path already falls back to the last `role=tool`
history entry when `msg.content` is empty. No code change needed — confirmed present.

#### P2: GOAT hallucinates when it lacks facts

**`supervisor/supervisor.py`**:
- `_unverified_summary` now includes the tool that was called (when available) in each
  failure line: `"researcher via web_search: web search returned an error"` instead of
  `"researcher: web search returned an error"`. Uses `AgentResult.tool_name`.
- After `synthesize_results`, if the returned summary is empty or whitespace, GOAT now
  sets `summary` to a factual fallback listing the tools that were called:
  `"Not available. Tools called: {tools}. No output from synthesis."` — no LLM call.
  Prevents silent empty responses reaching the interface.

**`supervisor/runners.py`**:
- `_run_summarizer`: added pre-check — if all upstream `dep_results` have empty outputs,
  the LLM is never called. Returns `"Not available. Upstream tasks returned no output."`
  immediately. Removes the fallback that previously called the LLM with empty context
  and could generate plausible-sounding but unverified content.

#### P3: Supervisor response discipline — explicit at all times

**`supervisor/identity.py`**:
- `GOAT_SYSTEM`: added `"no apologies"` to the no-filler rule so the constraint is
  unambiguous: `"No filler, no preamble, no apologies, no sign-offs."` Previously only
  `"no filler"` was listed, leaving apologies uncovered.

**`supervisor/critique.py`**:
- `synthesize_results` system prompt: added `"No apologies."` alongside the existing
  `"No headers, no tables, no preamble labels. No questions at the end."` rule.
  Synthesis LLM now has explicit guidance not to apologise for missing data.

All 37 existing tests pass. No imports broken. All modified files ≤200 lines with
docstrings on every function.
