"""Action log rendering — parse and format structured tool-call logs.

Extracted from ``supervisor/session/layer_renderer.py`` to keep
that file under the 260-line ceiling. This module owns the
end-to-end logic for rendering the per-turn action log inside
the [Present] layer:

  1. Identify records whose key ends with ``:actions``.
  2. Sort them by recency (newest first).
  3. Parse the JSON content into a list of dicts.
  4. Format each entry as ``tool(args) → ok/FAIL: summary``.
  5. Wrap under a ``Last turn actions:`` header.

Pure transformation — no I/O, no LLM, no memory-manager calls.
The orchestrator in ``layer_renderer.py`` calls into these
helpers and embeds the result in the rest of the [Present]
block.

USAGE:
    from supervisor.session.action_log_renderer import (
        extract_last_action_log,
        render_action_log_section,
    )
"""
from __future__ import annotations

import json
import logging
from typing import Final

log = logging.getLogger("goat2.supervisor.session.action_log_renderer")

__all__ = [
    "ACTION_LOG_KEY_SUFFIX",
    "ACTION_LOG_HEADER",
    "extract_last_action_log",
    "render_action_log_section",
]

# Records whose key ends with this suffix are treated as
# structured action-log entries. Matches the key used by
# supervisor.session.turn_persistence.store_action_log.
ACTION_LOG_KEY_SUFFIX: Final[str] = ":actions"

# Header printed in the [Present] block right above the action
# log lines. The model sees this label and knows to read the
# structured entries below — they're not free-text.
ACTION_LOG_HEADER: Final[str] = "Last turn actions:"


def _key_of(record) -> str:
    """Tolerant key accessor for both dict and SimpleNamespace shapes."""
    if isinstance(record, dict):
        return record.get("key", "") or ""
    return str(getattr(record, "key", "") or "")


def _created_at_ts(record) -> float:
    """Tolerant created_at_ts accessor (defaults to 0)."""
    if isinstance(record, dict):
        meta = record.get("metadata") or {}
    else:
        meta = getattr(record, "metadata", {}) or {}
    try:
        return float(meta.get("created_at_ts", 0))
    except (TypeError, ValueError):
        return 0.0


def is_action_log_record(record) -> bool:
    """True when the record is a structured action-log entry."""
    return _key_of(record).endswith(ACTION_LOG_KEY_SUFFIX)


def split_records_by_type(records: list) -> tuple[list, list]:
    """Split records into ``(action_records, other_records)``.

    Action records are sorted newest-first so the most recent
    action log is rendered. Other records keep their input order
    (the renderer will re-sort them by timestamp).
    """
    action_records: list = []
    other_records: list = []
    for r in records:
        if is_action_log_record(r):
            action_records.append(r)
        else:
            other_records.append(r)
    action_records.sort(key=_created_at_ts, reverse=True)
    return action_records, other_records


def extract_last_action_log(action_records: list) -> str:
    """Return the JSON content of the most recent action log record.

    Returns empty string when no action records exist OR when the
    content cannot be parsed. Caller is responsible for rendering
    the result.
    """
    if not action_records:
        return ""
    last = action_records[0]
    if isinstance(last, dict):
        return last.get("content") or ""
    return str(getattr(last, "content", "") or "")


def render_action_log_section(content_json: str) -> str:
    """Format one action-log JSON content as a renderable section.

    Returns:
        A multi-line string starting with ``Last turn actions:``,
        followed by indented entries. Empty string when the
        content is empty or unparseable.
    """
    if not content_json:
        return ""
    try:
        entries = json.loads(content_json)
    except (TypeError, ValueError) as exc:
        log.debug("action_log: invalid JSON content (%s); skipping", exc)
        return ""

    if not isinstance(entries, list) or not entries:
        return ""

    # Import here to avoid an import cycle (turn_persistence
    # imports from session, not from action_log_renderer).
    from supervisor.session.turn_persistence import format_action_log

    body = format_action_log(entries)
    if not body:
        return ""

    lines = [ACTION_LOG_HEADER]
    for action_line in body.split("\n"):
        lines.append(f"  {action_line}")
    return "\n".join(lines)