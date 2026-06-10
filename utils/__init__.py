"""Utility functions for GOAT 2.0 — LLM client, message formatting, JSON extraction.

This module provides shared utilities used across the supervisor, agents,
and tools modules.

EXPORTS:
=======
- _get_client: Cached AsyncOpenAI client per provider
- _call_llm: Send messages to LLM, return text content
- _extract_json: Extract JSON from text with multiple fallback parsers
- _extract_balanced_json: Extract JSON using brace-balance counting
- _format_dep_context: Format AgentResults as Markdown for context
- _model_label: Get model label for a role
- _truncate_content: Truncate content to max chars
- _truncate_messages: Ensure all messages respect size limits
"""
from utils.llm_utils import (
    _get_client,
    _call_llm,
    _extract_json,
    _extract_balanced_json,
    _format_dep_context,
    _model_label,
    _truncate_content,
    _truncate_messages,
)

__all__ = [
    "_get_client",
    "_call_llm",
    "_extract_json",
    "_extract_balanced_json",
    "_format_dep_context",
    "_model_label",
    "_truncate_content",
    "_truncate_messages",
]