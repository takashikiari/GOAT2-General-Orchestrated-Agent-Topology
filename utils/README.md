# utils — Shared utility functions for GOAT 2.0

This module provides LLM client utilities and message formatting functions
used across the supervisor, agents, and tools modules.

## Purpose

Centralizes common LLM operations to avoid code duplication:
- Cached OpenAI client management
- Message size limits to prevent API errors
- JSON extraction with multiple fallback parsers
- Context formatting for agent dependencies

## Exports

| Function | Description |
|----------|-------------|
| `_get_client(spec)` | Return cached AsyncOpenAI client for provider |
| `_call_llm(spec, messages, ...)` | Send messages to LLM, return text |
| `_extract_json(text)` | Extract JSON from text with fallbacks |
| `_extract_balanced_json(text)` | Extract JSON using brace counting |
| `_format_dep_context(results)` | Format AgentResults as Markdown |
| `_model_label(role)` | Get model label for role |
| `_truncate_content(content, max)` | Truncate to max chars |
| `_truncate_messages(messages)` | Enforce size limits on messages |

## Size Limits

To prevent "Message is too long" API errors:

- `_MAX_CONTENT_CHARS`: 64000 per message
- `_MAX_CONTEXT_CHARS`: 32000 total for dep context
- `_MAX_RESULT_CHARS`: 4000 per individual result
- `_MAX_TOTAL_MESSAGES_CHARS`: 128000 total across messages

## Routing Pattern

`utils/` is a shared leaf module — it may be imported by `agents/`, `supervisor/`, and `tools/` without creating circular imports.

Rules applied in this module:
- `from __future__ import annotations` in every file (type hints as strings, no runtime eval)
- Cross-layer type hints (`AgentResult` from `supervisor.types`) are placed under `if TYPE_CHECKING:` — they only resolve during type checking, never at import time
- No module-level imports from `agents/`, `supervisor/`, or `tools/` — `config.settings` is the only cross-module import (it is a leaf module with no transitive dependencies on the three main layers)
- `_get_client` imports `Settings` inside the function body as a safeguard against future circular chains

## Debug Logger Namespaces

| File | Logger name |
|------|-------------|
| `utils/__init__.py` | `goat2.utils` |
| `utils/llm_utils.py` | `goat2.utils.llm_utils` |

Enable per-module DEBUG output:

```python
import logging
logging.getLogger("goat2.utils.llm_utils").setLevel(logging.DEBUG)
```

## Usage

```python
from utils.llm_utils import _call_llm, _format_dep_context
from utils import _get_client
```