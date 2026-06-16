"""Backward-compat re-export of the single GOAT decision result.

Canonical home: ``supervisor.pipeline.goat_call``. New code should
import ``GoatTurnResult`` and ``goat_turn`` directly from there.
This module is kept as a thin re-export shim so existing imports
(``from supervisor.pipeline.goat_decision import decide``,
``GoatDecision``) keep working unchanged.

``decide`` here is the legacy two-call shim: it calls
``goat_turn`` internally for backward-compat with any code that
still imports the old name. New code should use ``goat_turn``
directly.

``GoatDecision`` is a backward-compat alias for ``GoatTurnResult``
(``GoatTurnResult`` has the same ``action`` / ``response`` /
``clarification`` / ``dag_instructions`` fields plus a few extras
the merged call needs). Mapping the type preserves
``isinstance`` checks in old call sites.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from supervisor.pipeline.goat_call import GoatTurnResult, goat_turn

if TYPE_CHECKING:
    from config.registry import ServiceRegistry
    from supervisor.pipeline.goat_enrichment import GoatContext
    from supervisor.pipeline.intent_clarity import ClarityContext

log = logging.getLogger("goat2.supervisor.pipeline.goat_decision")

__all__ = ["GoatDecision", "decide"]


# GoatDecision is the old single-call decision dataclass. It is
# field-compatible with GoatTurnResult so the alias preserves
# every existing call site (isinstance checks, attribute access).
GoatDecision = GoatTurnResult


async def decide(
    registry: "ServiceRegistry",
    intent: str,
    goat_context: "GoatContext",
    clarity_context: "ClarityContext",
    hints: list[str],
) -> GoatTurnResult:
    """Backward-compat shim — delegates to ``goat_call.goat_turn``.

    The single-call architecture merged ``decide`` +
    ``direct_response`` into one tool-enabled LLM call. The
    legacy name is preserved here as a thin re-export so existing
    imports (``from supervisor.pipeline.goat_decision import
    decide``) keep working unchanged. New code should call
    ``goat_turn`` directly.

    The new signature accepts the same five positional args as
    the old ``decide`` and returns a ``GoatTurnResult`` (alias
    for ``GoatDecision``). Extra fields (``history_messages``,
    ``mem_ctx``, ``profile``, etc.) default to empty / no-op so
    callers that only pass the original five args still get a
    useful reply.
    """
    return await goat_turn(
        registry, intent, goat_context, clarity_context, hints,
        history_messages=[],
        mem_ctx="",
    )
