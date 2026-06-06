# Changelog

All notable changes to GOAT 2.0 are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-06-05 (patch 49)

### Fixed

#### Module-level docstrings added to all tool files

Every file in `tools/` now has a standard English docstring at the top describing the
module's purpose and primary functionality. Files modified:

| File | Docstring |
|------|-----------|
| `tools/__init__.py` | Tool registry — exports all tool definitions and convenience groupings |
| `tools/calculator.py` | Safe arithmetic expression evaluator using AST parsing |
| `tools/memory_temporal_tools.py` | Temporal memory query tools — timeline, recent, and debug trace |
| `tools/memory_tools.py` | Memory CRUD tools — search, get, and store across memory tiers |
| `tools/think.py` | Chain-of-thought reasoning tool — records a private reasoning step |
| `tools/web_search.py` | Web search tool — queries DuckDuckGo instant answers (or custom backend) |

No functional code was changed — only docstrings were added.

#### `file_storage_service.py` refactored to under 200 lines

The file was rewritten to import shared logic from `file_storage_helpers.py`:

- **`FileStorageService`** — abstract base class with `save`, `read`, `read_stream`,
  `delete`, `exists`, `size`, `list_keys` interface
- **`LocalFileStorage`** — filesystem backend with path traversal protection
- **`S3FileStorage`** — S3-compatible object storage backend (optional)
- **`get_storage_backend()`** — factory function selecting backend via config/env

Path resolution, error types, and factory logic now live in `file_storage_helpers.py`,
keeping the service file focused on the storage abstraction layer.

---

## [Unreleased] — 2026-06-05 (patch 48)

### Added

#### Temporal memory search — `memory_timeline`, `memory_recent`, `memory_debug_trace`; extended `memory_search`

... (see previous entries)
