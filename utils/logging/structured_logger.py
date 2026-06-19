"""Structured tool-call logger — emits a single log line per
tool invocation with stable, parseable fields.

Pure Python, no LLM, no I/O of its own. Uses the standard
``logging`` module so it integrates with the application's
existing log handlers. The output is a single ``INFO``-level
record containing the tool name, a short params preview, the
source tag, and a short response hash prefix for quick audit
correlation.

USAGE:
    from utils.logging.structured_logger import log_tool_call

    log_tool_call(
        tool_name="web_search",
        params={"query": "GOAT 2.0", "limit": 5},
        source="net",
        response_hash="3a4f...",
    )

The function is deliberately side-effect-free aside from
emitting one log record. It never raises — defensive by
design so a logging failure cannot break the tool-calling
loop.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Final

__all__ = ["log_tool_call", "hash_response"]


# Stable logger name. Importers should NOT re-export this
# directly — they should call ``log_tool_call`` instead.
log = logging.getLogger("goat2.utils.logging.structured")

# Maximum length of the params preview (chars). Keeps the
# log line bounded for huge tool arguments.
_PARAMS_PREVIEW_CHARS: Final[int] = 200
# Maximum length of the response hash we print (chars). We
# always emit a 16-char prefix; longer hashes are truncated.
_HASH_PREFIX_CHARS: Final[int] = 16


def hash_response(response: str) -> str:
    """Return a 16-char SHA-256 hex prefix of ``response``.

    A short prefix is enough to correlate two log lines
    ("same response") without leaking the response content
    into the log stream. The hash is deterministic; callers
    that need the full digest can compute their own SHA-256
    and truncate.
    """
    if response is None:
        return ""
    digest = hashlib.sha256(response.encode("utf-8", errors="replace")).hexdigest()
    return digest[:_HASH_PREFIX_CHARS]


def _params_preview(params: Any) -> str:
    """Render ``params`` as a short, log-safe string.

    Dicts are JSON-serialized (sorted, ASCII-safe) and
    truncated. Non-dict values are ``str()``-ed. ``None``
    becomes ``"-"``.
    """
    if params is None:
        return "-"
    if isinstance(params, dict):
        try:
            text = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(params)
    else:
        text = str(params)
    if len(text) > _PARAMS_PREVIEW_CHARS:
        text = text[:_PARAMS_PREVIEW_CHARS] + "…"
    return text


def log_tool_call(
    tool_name: str,
    params: Any,
    source: str,
    response_hash: str,
) -> None:
    """Emit one structured INFO log line for a tool invocation.

    Format (single line, space-separated, no newlines in
    fields):

        tool_call name=<name> source=<source> hash=<hash> params=<preview>

    Args:
        tool_name: The tool's registered name (e.g. ``"web_search"``).
        params: The arguments the LLM passed. Dict-shaped
            arguments are JSON-rendered with sorted keys so
            the log line is stable across runs.
        source: The source tag (``"net"``, ``"file"``, ...).
            See ``source_types.SourceTag``.
        response_hash: A short hash prefix identifying the
            response. Use ``hash_response(result)`` to compute.

    Returns:
        None. Best-effort: any exception during logging is
        swallowed so the tool-calling loop is never affected.
    """
    try:
        log.info(
            "tool_call name=%s source=%s hash=%s params=%s",
            tool_name or "?",
            source or "?",
            (response_hash or "")[:_HASH_PREFIX_CHARS] or "-",
            _params_preview(params),
        )
    except Exception:  # noqa: BLE001 — logging must never break the loop
        # Swallow silently; the audit trail can reconstruct
        # what happened from the surrounding context.
        return