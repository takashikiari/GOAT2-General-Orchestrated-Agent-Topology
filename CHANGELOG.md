# Changelog

All notable changes to GOAT 2.0 are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-07-07

### Fixed

- **GLiNER truncation** (`memory/gliner_extractor.py`): long texts (>200 words) were silently truncated to 384 tokens by the model. Added `_chunk_text` that splits into ≤200-word chunks, runs NER on each, and merges results with deduplication. Entities from the full text are now extracted correctly.

- **Prefetch timeout loop** (`orchestrator/orchestrator.py`, `orchestrator/prefetch.py`): `asyncio.wait_for` was cancelling the prefetch task on timeout, leaving the activation unsaved and forcing every subsequent turn cold. Replaced with `asyncio.wait` (which never cancels tasks) + `save_prefetch_background` that awaits the still-running task and saves activation so the next turn is warm.

- **auto_promote every-turn ping-pong** (`memory/auto_promote.py`): at steady state (conversation at cap) every turn added 2 messages then immediately dropped 2, running GLiNER enrichment on every single turn. Added `PROMOTE_MIN_SURPLUS = 4` threshold — trim only fires when surplus ≥ 4, halving enrichment frequency with negligible working-memory overhead.

### Refactored

- **Prefetch extracted from orchestrator** (`orchestrator/prefetch.py`, `orchestrator/activation_manager.py`): the prefetch daemon (`_prefetch_daemon`), background save (`_save_prefetch_background`), and activation persistence (`_update_activation`) were private methods of `Orchestrator`. Extracted to two single-responsibility modules:
  - `orchestrator/prefetch.py` — `run_prefetch` (warm/drift/cold search logic) + `save_prefetch_background`
  - `orchestrator/activation_manager.py` — `update_activation`
  - `Orchestrator` now calls these as plain functions, passing `layers` explicitly — no singletons, no registry coupling, no lazy imports.

---

## [0.1.1] — prior

See git log for earlier changes.
