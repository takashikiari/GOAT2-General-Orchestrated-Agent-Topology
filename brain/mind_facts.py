"""brain.mind_facts — the L1 facts inside the working memory (spec 2026-11-21 §2 F1, F7; polish P1).

Two kinds, like procedural and semantic memory: a ``regulă`` (how GOAT must behave: style, address, emoji,
permissions, workflow rules) is shown on every turn; a ``cunoștință`` (knowledge about the user, people, machines,
projects) only when the message is about it, and it stays in mind for the follow-up turns as the item
``fact:<key>`` (kind ``fapt``), fading with the rest. The kinds are decided by ONE LLM call in the sleep tick, never
on the reply path, and kept in ``fact_kind`` with a hash of the value they were decided on: a changed value is
classified again, so a misclassified rule is recoverable (Q2); a key the model keeps leaving out is asked at most
_MAX_ATTEMPTS times per value in this process. A fact not classified yet counts as a rule (always shown: nothing is
lost). A row whose key left L1 stays, unused. Nothing is ever deleted.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import weakref
from typing import Any

from brain import mind
from brain.mind_text import has_word
from brain.understand import _ask, _fill
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["KINDS", "PREFIX", "value_hash", "kinds", "split", "parse", "save", "classify", "light", "in_mind",
           "mentioned", "matching"]

KINDS = ("regulă", "cunoștință")
PREFIX = "fact:"
KEEP = 0.3  # a fact lit at 1.0 stays shown while carry keeps it above this: the next two turns (0.6, 0.36)
_VALUE_MAX = 200
_MAX_ATTEMPTS = 3  # per key and value, in this process (like R34): a key the model keeps omitting is not asked forever
_ATTEMPTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # brain -> {(key, value hash): times asked}

CLASSIFY_PROMPT = """Ești partea din memoria lui GOAT care își sortează faptele permanente. Primești faptele, \
câte unul pe linie, în forma `cheie: valoare`. Pentru fiecare spune ce fel de fapt este:
- "regulă": tot ce schimbă cum îi răspunde GOAT utilizatorului sau cum i se adresează, și tot ce trebuie știut la \
fiecare replică: numele utilizatorului, limba, rolul și echipa lui, permisiunile, stilul, emoji, reguli de lucru;
- "cunoștință": doar ce trebuie știut când vine vorba de un anumit subiect: alți oameni, mașini, locuri, proiecte, \
întâmplări.
Dacă nu ești sigur, alege „regulă”.

Răspunde cu UN SINGUR obiect JSON, fără text în afara lui, în care fiecare cheie de fapt are valoarea \
"regulă" sau "cunoștință".

Fapte:
{facts}
"""


def value_hash(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


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


def save(db, got: dict[str, str], now: float, values: dict | None = None) -> None:
    """Keep each kind with the hash of the value it was decided on (``values``: the facts; none -> NULL, asked
    again); a key classified before is updated in place, never removed."""
    for key, kind in got.items():
        vh = value_hash(values[key]) if values and key in values else None
        db.execute("INSERT INTO fact_kind (key, kind, classified_at, value_hash) VALUES (?, ?, ?, ?) ON CONFLICT(key) "
                   "DO UPDATE SET kind = excluded.kind, classified_at = excluded.classified_at, "
                   "value_hash = excluded.value_hash", (key, kind, now, vh))
    db.commit()


def _hashes(db) -> dict[str, str | None]:
    return {k: h for k, h in db.execute("SELECT key, value_hash FROM fact_kind").fetchall()}


async def classify(brain, client, facts: dict) -> int:
    """Classify the L1 facts with no kind yet or whose value changed since, all in one LLM call (outside
    ``brain.lock``); returns how many were classified. Never raises: a failure leaves them as they were (an
    unclassified one shown as a rule) for the next tick, at most _MAX_ATTEMPTS asks per key and value."""
    try:
        async with brain.lock:
            known = await asyncio.to_thread(_hashes, brain._db)
        tries = _ATTEMPTS.setdefault(brain, {})
        keys = [k for k, v in (facts or {}).items() if known.get(k) != value_hash(v)
                and tries.get((k, value_hash(v)), 0) < _MAX_ATTEMPTS]
        if not keys:
            return 0
        for k in keys:
            tries[(k, value_hash(facts[k]))] = tries.get((k, value_hash(facts[k])), 0) + 1
        body = "\n".join(f"{k}: {str(facts[k])[:_VALUE_MAX]}".replace("\n", " ") for k in keys)
        got = parse(await _ask(client, _fill(CLASSIFY_PROMPT, facts=body), max_tokens=8000), keys)
        if got:
            async with brain.lock:
                await asyncio.to_thread(save, brain._db, got, time.time(), facts)
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
