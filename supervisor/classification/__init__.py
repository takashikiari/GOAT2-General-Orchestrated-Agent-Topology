"""supervisor.classification — pure-Python routing logic.

Three modules:
  - ``classifier``     — turn action → routing depth (DIRECT/CLARIFY/DAG)
  - ``lang_detect``    — Romanian / English / mixed detection
  - ``intent_clarity`` — missing-slot detection (path / format / scope / name)

No LLM, no regex, no I/O. The LLM call lives in
``supervisor.pipeline.goat_call``; this package only consumes
its result and tells the rest of the system what kind of turn
it was.
"""
from __future__ import annotations

from supervisor.classification import classifier, intent_clarity, lang_detect

__all__ = ["classifier", "intent_clarity", "lang_detect"]
