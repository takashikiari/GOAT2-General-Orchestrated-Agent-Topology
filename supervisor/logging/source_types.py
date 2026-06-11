"""Source provenance types for GOAT 2.0 tool traceability.

Defines SourceTag, TaggedResult, TOOL_SOURCE_MAP, and infer_source for
propagating data provenance from tool calls through the DAG to supervisor output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal

log = logging.getLogger("goat2.supervisor.logging")

__all__ = ["SourceTag", "TaggedResult", "TOOL_SOURCE_MAP", "infer_source"]

SourceTag = Literal["net", "memory", "file", "generated"]

TOOL_SOURCE_MAP: Final[dict[str, SourceTag]] = {
    "web_search":         "net",
    "memory_search":      "memory",
    "memory_get":         "memory",
    "memory_store":       "memory",
    "memory_timeline":    "memory",
    "memory_recent":      "memory",
    "memory_debug_trace": "memory",
    "file_read":          "file",
    "file_write":         "file",
    "file_create":        "file",
    "file_list":          "file",
    "file_search":        "file",
    "file_grep":          "file",
    "file_info":          "file",
    "file_read_lines":    "file",
}


def infer_source(called_tools: list[str]) -> SourceTag:
    """Derive the dominant data source from the list of tools that were actually called.

    Priority order: net > memory > file > generated.
    Returns 'generated' when no tools were called.
    """
    for src in ("net", "memory", "file"):
        for tool in called_tools:
            if TOOL_SOURCE_MAP.get(tool) == src:
                return src  # type: ignore[return-value]
    return "generated"


@dataclass(frozen=True)
class TaggedResult:
    """Tool-calling result bundled with data provenance.

    Attributes:
        content: The string output to return to the caller.
        source: Dominant data source tag (net / memory / file / generated).
        called_tools: Names of every tool invoked during this LLM call, in order.
    """

    content: str
    source: SourceTag
    called_tools: tuple[str, ...] = ()
