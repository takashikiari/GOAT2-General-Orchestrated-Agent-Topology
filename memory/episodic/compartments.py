"""Episodic compartments — organization of medium-term memory.

Episodic entries are grouped into compartments so context can be injected in a
clear, labelled way. The compartment is encoded in the key as a
``<compartment>:<key>`` prefix and mirrored into entry metadata.

Compartment detection is deterministic categorization (NOT relevance scoring): it
reads the explicit ``<compartment>:`` prefix when present, otherwise falls back to
a small documented map of legacy key prefixes. Relevance scoring (the sliding
window) stays pure LLM and lives elsewhere.

Access rules:
  - GOAT (supervisor): all compartments.
  - DAG agents: none — they are restricted to the working tier (already enforced
    by the tiered-access model; documented here for clarity).
"""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger("goat2.memory.episodic.compartments")

__all__ = [
    "EpisodicCompartment",
    "namespaced_key",
    "compartment_for_key",
    "GOAT_COMPARTMENT_ACCESS",
    "DAG_COMPARTMENT_ACCESS",
]


class EpisodicCompartment(str, Enum):
    """The four episodic compartments."""

    TURNS       = "turns"        # conversation turns promoted from working memory
    PREFERENCES = "preferences"  # user preferences and corrections
    DAG_RESULTS = "dag_results"  # relevant DAG execution results
    CORRECTIONS = "corrections"  # behavioral-learning corrections


# Access matrix (documentation of the tiered-access model).
GOAT_COMPARTMENT_ACCESS: tuple[EpisodicCompartment, ...] = tuple(EpisodicCompartment)
DAG_COMPARTMENT_ACCESS: tuple[EpisodicCompartment, ...] = ()

# Legacy key-prefix → compartment map, used only when a key lacks an explicit
# ``<compartment>:`` prefix. Categorization, not relevance.
_LEGACY_PREFIXES: tuple[tuple[str, EpisodicCompartment], ...] = (
    ("turn", EpisodicCompartment.TURNS),
    ("preference", EpisodicCompartment.PREFERENCES),
    ("dag", EpisodicCompartment.DAG_RESULTS),
    ("correction", EpisodicCompartment.CORRECTIONS),
)

_VALID = {c.value for c in EpisodicCompartment}


def namespaced_key(compartment: EpisodicCompartment | str, key: str) -> str:
    """Return ``<compartment>:<key>`` (idempotent if already namespaced)."""
    comp = compartment.value if isinstance(compartment, EpisodicCompartment) else str(compartment)
    head, sep, _ = key.partition(":")
    if sep and head in _VALID:
        return key
    return f"{comp}:{key}"


def compartment_for_key(key: str) -> str:
    """Infer the compartment value for ``key`` (deterministic, no LLM).

    Uses the explicit ``<compartment>:`` prefix when present; otherwise the legacy
    prefix map; otherwise defaults to TURNS.
    """
    head, sep, _ = key.partition(":")
    if sep and head in _VALID:
        return head
    lowered = key.lower()
    for prefix, comp in _LEGACY_PREFIXES:
        if lowered.startswith(prefix):
            log.debug("compartment_for_key: %s → %s (legacy prefix)", key, comp.value)
            return comp.value
    return EpisodicCompartment.TURNS.value
