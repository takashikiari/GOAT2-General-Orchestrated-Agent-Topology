"""Utility functions for GOAT 2.0 — LLM client, message formatting, JSON extraction."""
from __future__ import annotations

import logging

log = logging.getLogger("goat2.utils")

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