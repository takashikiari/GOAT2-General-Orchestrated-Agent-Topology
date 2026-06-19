"""utils.logging — structured logging, source tagging, and
post-run audit for the agentic tool-calling loop.

Pure Python, no LLM, no I/O. Four small modules:

  - ``setup``             — ``configure_logging`` (file + stderr
                            handlers, idempotent)
  - ``source_types``      — ``TaggedResult``, ``SourceTag``,
                            ``TOOL_SOURCE_MAP``, ``infer_source``
  - ``structured_logger`` — ``log_tool_call`` (one-line
                            structured INFO record per call)
  - ``auditor``           — ``run_auditor`` (post-DAG rule-
                            based audit) + ``AuditReport``

Layering:
    The package sits at the bottom of the dependency graph.
    ``tools/`` and ``supervisor/`` may import from it freely;
    it does NOT import from either. This avoids the
    ``tools → supervisor → tools`` cycle that motivated the
    module's original location under ``supervisor/logging/``.

USAGE:
    from utils.logging.setup import configure_logging
    configure_logging()                                # entry point
    from utils.logging.source_types import TaggedResult, infer_source
    from utils.logging.structured_logger import log_tool_call
    from utils.logging.auditor import run_auditor, AuditReport
"""
from __future__ import annotations

from utils.logging import auditor, setup, source_types, structured_logger

__all__ = ["auditor", "setup", "source_types", "structured_logger"]
