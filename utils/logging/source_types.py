"""Tool-call source tagging — types and inference for the
agentic tool-calling loop.

Pure Python, no LLM, no I/O. Provides the data shape
``TaggedResult`` that the tool runner returns, the canonical
``SourceTag`` constants, the per-tool ``TOOL_SOURCE_MAP`` that
maps tool names to their source tag, and the ``infer_source``
function that collapses a list of called-tool names to a
single source tag.

USAGE:
    from utils.logging.source_types import (
        TaggedResult, SourceTag, TOOL_SOURCE_MAP, infer_source,
    )

    result = TaggedResult(
        content="hello",
        called_tools=("web_search",),
        source=infer_source(["web_search"]),   # → "net"
    )

SOURCE TAGS:
    - ``generated`` — the LLM produced the answer (no useful
      external tool ran, or the tool was a pure helper like
      ``calculator``).
    - ``file``     — file-system read/write tools (``read_file``,
      ``write_file``, ``list_directory``).
    - ``net``      — network tools (``web_search`` and friends).
    - ``memory``   — memory-tier tools (``memory_store``,
      ``memory_retrieve``).
    - ``tool``     — generic tool calls that don't fit the
      above categories (default for unknown names).

The constants are plain strings so they can be stored on disk,
serialized over the wire, and matched without re-rendering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "TaggedResult",
    "SourceTag",
    "GENERATED",
    "FILE",
    "NET",
    "MEMORY",
    "TOOL",
    "TOOL_SOURCE_MAP",
    "infer_source",
]


# ── Source tag constants ────────────────────────────────────────
# Plain strings, not an Enum: callers pattern-match on these
# exact tokens across the codebase and over the wire, so
# string stability matters more than type-safety.

GENERATED: Final[str] = "generated"
FILE:      Final[str] = "file"
NET:       Final[str] = "net"
MEMORY:    Final[str] = "memory"
TOOL:      Final[str] = "tool"

# Type alias for callers that want to spell it out.
SourceTag = str


# ── TOOL_SOURCE_MAP ──────────────────────────────────────────────
# Per-tool mapping. Unknown tools default to GENERATED via
# ``infer_source`` (so the agentic loop never produces an
# un-tagged result). The set is intentionally conservative —
# only tools that have a single, unambiguous source category
# are listed. Generic / control tools (think, calculator) are
# also mapped to GENERATED so a run that only called them is
# still correctly attributed.

TOOL_SOURCE_MAP: Final[dict[str, str]] = {
    # File-system tools.
    "read_file":          FILE,
    "write_file":         FILE,
    "list_directory":     FILE,
    "directory_tree":    FILE,
    "create_directory":   FILE,
    "delete_file":        FILE,
    "delete_directory":   FILE,
    "apply_patch":        FILE,
    "patch_file":         FILE,
    "move_file":          FILE,
    "copy_file":          FILE,
    # Network tools.
    "web_search":         NET,
    "http_get":           NET,
    "http_post":          NET,
    "fetch_url":          NET,
    "curl":               NET,
    # Memory tools (working/episodic/long_term).
    "memory_store":       MEMORY,
    "memory_retrieve":    MEMORY,
    "memory_search":      MEMORY,
    "memory_clear":       MEMORY,
    "memory_list":        MEMORY,
    "memory_last_write":  MEMORY,
    # Generic / control helpers → generated.
    "calculator":         GENERATED,
    "think":              GENERATED,
    "read_logs":          TOOL,
}


@dataclass
class TaggedResult:
    """Result of a tool-calling LLM loop run.

    Attributes:
        content: The visible text the LLM produced (DSML
            markers stripped, tool-result tails resolved).
        called_tools: Tuple of every tool name the LLM invoked
            during the run, in invocation order. Empty when
            the LLM answered without tools.
        source: Inferred source tag (one of the ``SourceTag``
            constants). Defaults to ``"generated"``.
    """

    content:      str
    called_tools: tuple[str, ...] = field(default_factory=tuple)
    source:       str              = GENERATED


def infer_source(called_tools: list[str] | tuple[str, ...]) -> str:
    """Map a list of called tool names to a single source tag.

    Aggregation rule:
      - Empty list → ``GENERATED`` (the LLM answered with no
        tool calls at all).
      - Otherwise, the most-specific category wins. The
        priority order is ``MEMORY > NET > FILE > TOOL > GENERATED``
        — memory results are the most "grounded" and
        override everything else; network results beat file
        results; etc. A run that called both ``web_search``
        and ``read_file`` is attributed to ``net`` because
        the network result is the more meaningful one.
      - Unknown tool names are looked up in
        ``TOOL_SOURCE_MAP`` and default to ``GENERATED`` if
        absent (so the result is never untagged).

    Args:
        called_tools: Sequence of tool names invoked during
            the run. Order does not matter — the function
            counts occurrences.

    Returns:
        One of the ``SourceTag`` strings.
    """
    if not called_tools:
        return GENERATED
    # Count sources across all calls; pick the highest-priority
    # one that was used at least once. This handles the common
    # case where a single tool was called multiple times as
    # well as mixed runs.
    seen: set[str] = set()
    for name in called_tools:
        if not isinstance(name, str) or not name:
            continue
        seen.add(TOOL_SOURCE_MAP.get(name, GENERATED))
    # Priority order — first match wins.
    for priority in (MEMORY, NET, FILE, TOOL, GENERATED):
        if priority in seen:
            return priority
    # Defensive fallback (empty / non-string entries only).
    return GENERATED