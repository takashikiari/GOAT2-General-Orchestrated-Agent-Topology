"""Structured logging and audit for GOAT 2.0 — tool call traceability and cross-task consistency.

Exports:
    - AuditReport, run_auditor: Cross-tool consistency check
    - log_tool_call: Structured JSON logging for tool invocations
    - SourceTag, TaggedResult: Data provenance types
    - TOOL_SOURCE_MAP, infer_source: Tool-to-source mapping
"""
from supervisor.logging.auditor import AuditReport, run_auditor
from supervisor.logging.structured_logger import log_tool_call
from supervisor.logging.source_types import SourceTag, TaggedResult, TOOL_SOURCE_MAP, infer_source

__all__ = [
    "AuditReport",
    "run_auditor",
    "log_tool_call",
    "SourceTag",
    "TaggedResult",
    "TOOL_SOURCE_MAP",
    "infer_source",
]