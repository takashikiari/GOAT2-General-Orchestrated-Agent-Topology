"""brain.mind_facts — the L1 facts inside the working memory (spec 2026-11-21 §2 F1, F7; polish P1).

Two kinds, like procedural and semantic memory: a ``regulă`` (how GOAT must behave: style, address, emoji,
permissions, workflow rules) is shown on every turn; a ``cunoștință`` (knowledge about the user, people, machines,
projects) only when the message is about it, and it stays in mind for the follow-up turns as the item
``fact:<key>`` (kind ``fapt``), fading with the rest. The kinds are decided by ONE LLM call in the sleep tick, never
on the reply path, and kept in ``fact_kind``; a fact not classified yet counts as a rule (always shown: nothing is
lost). A row whose key left L1 stays, unused. Nothing is ever deleted.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from brain import mind
from brain.mind_text import has_word
from brain.understand import _ask, _fill
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["KINDS", "PREFIX", "kinds", "split", "parse", "save", "classify", "light", "in_mind", "mentioned", "matching"]

KINDS = ("regulă", "cunoștință")
PREFIX = "fact:"
KEEP = 0.3  # a fact lit at 1.0 stays shown while carry keeps it above this: the next two turns (0.6, 0.36)
_VALUE_MAX = 200

CLASSIFY_PROMPT = """Ești partea din memoria lui GOAT care își sortează faptele permanente. Primești faptele, \
câte unul pe linie, în forma `cheie: valoare`. Pentru fiecare spune ce fel de fapt este:
- "regulă": cum trebuie să se poarte GOAT (stil, adresare, emoji, permisiuni, reguli de lucru);
- "cunoștință": ceva știut despre utilizator, despre oameni, mașini, locuri sau proiecte.

Răspunde cu UN SINGUR obiect JSON, fără text în afara lui, în care fiecare cheie de fapt are valoarea \
"regulă" sau "cunoștință".

Fapte:
{facts}
"""


def kinds(db) -> dict[str, str]:
    """{fact key: its kind} for every fact ever classified."""
    return {k: v for k, v in db.execute("SELECT key, kind FROM fact_kind").fetchall()}


def split(facts: dict, known: dict[str, str]) -> tuple[dict, dict]:
    """(rules, knowledge) of ``facts``, both in L1 order; a fact with no kind yet is a rule."""
    rules = {k: v for k, v in (facts or {}).items() if known.get(k) != "cunoștință"}
    return rules, {k: v for k, v in (facts or {}).items() if k not in rules}


def parse(obj: Any, keys: list[str]) -> dict[str, str]:
    """The classification answer, kept only for the keys asked and the two kinds; anything else stays unclassified."""
    if not isinstance(obj, dict):
        return {}
    return {k: obj[k] for k in keys if obj.get(k) in KINDS}


def save(db, got: dict[str, str], now: float) -> None:
    for key, kind in got.items():
        db.execute("INSERT OR IGNORE INTO fact_kind (key, kind, classified_at) VALUES (?, ?, ?)", (key, kind, now))
    db.commit()


async def classify(brain, client, facts: dict) -> int:
    """Classify the L1 facts that have no kind yet, all in one LLM call (outside ``brain.lock``); returns how many
    were classified. Never raises: a failure leaves them unclassified (shown as rules) for the next tick."""
    try:
        async with brain.lock:
            known = await asyncio.to_thread(kinds, brain._db)
        keys = [k for k in facts or {} if k not in known]
        if not keys:
            return 0
        body = "\n".join(f"{k}: {str(facts[k])[:_VALUE_MAX]}".replace("\n", " ") for k in keys)
        got = parse(await _ask(client, _fill(CLASSIFY_PROMPT, facts=body), max_tokens=8000), keys)
        if got:
            async with brain.lock:
                await asyncio.to_thread(save, brain._db, got, time.time())
        log.info("brain: facts classified=%d of %d", len(got), len(keys))
        return len(got)
    except Exception as exc:  # noqa: BLE001 - the sleep must never stop on this
        log.warning("brain: fact classification failed: %s: %s", type(exc).__name__, exc)
        return 0


def light(db, chat_id: str, lane: str, keys: list[str], now: float) -> None:
    """The knowledge facts the message is about come into mind at full activation (committed by the caller)."""
    for key in keys:
        mind.light(db, chat_id, lane, PREFIX + key, "fapt", 1.0, "înțelegere", "despre ce întrebi", now, commit=False)


def in_mind(db, chat_id: str, lane: str, knowledge: dict) -> dict:
    """The knowledge facts in mind now (activation >= KEEP), in L1 order; a key no longer in L1 is not shown."""
    lit = {r["item"][len(PREFIX):] for r in mind.top(db, chat_id, lane, 10_000, KEEP) if r["kind"] == "fapt"}
    return {k: v for k, v in (knowledge or {}).items() if k in lit}


def mentioned(facts: dict, name: str) -> bool:
    """True when ``name`` is a whole word of the key (its "_" read as spaces) or value of an L1 fact, case and
    diacritics aside (F7, Q4: it is known, so not missing)."""
    return bool(matching(facts, [name]))


def matching(facts: dict, names: list[str]) -> list[str]:
    """The keys of the facts (in L1 order) whose key or value names one of ``names`` (see ``mentioned``)."""
    return [k for k, v in (facts or {}).items()
            if any(has_word(f"{str(k).replace('_', ' ')} {v}", str(n)) for n in names or [])]
