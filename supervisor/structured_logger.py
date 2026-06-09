"""Structured JSON logging for GOAT 2.0 tool calls.

Emits one JSON-serialized log record per tool invocation to the
'goat2.tool_calls.structured' logger. Does not affect or replace any
existing text-based logging in goat2.supervisor or goat2.file_executor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

__all__ = ["log_tool_call"]

_struct_log = logging.getLogger("goat2.tool_calls.structured")


def _hash_response(response: str) -> str:
    """Return a 16-char SHA-256 hex prefix for audit deduplication."""
    return hashlib.sha256(response.encode()).hexdigest()[:16]


def log_tool_call(
    tool_name: str,
    params: dict[str, Any],
    source: str,
    response: str,
) -> None:
    """Emit one structured JSON log record for a single tool call invocation.

    Fields emitted: tool_name, params, source, timestamp (Unix epoch float),
    response_hash (SHA-256 prefix). Errors are silenced so logging never
    crashes the calling thread.
    """
    try:
        record: dict[str, Any] = {
            "tool_name":     tool_name,
            "params":        params,
            "source":        source,
            "timestamp":     time.time(),
            "response_hash": _hash_response(response),
        }
        _struct_log.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    except Exception:  # pragma: no cover
        pass
