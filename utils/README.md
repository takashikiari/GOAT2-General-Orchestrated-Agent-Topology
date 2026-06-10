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

## Usage

```python
from utils.llm_utils import _call_llm, _format_dep_context
from utils import _get_client
```