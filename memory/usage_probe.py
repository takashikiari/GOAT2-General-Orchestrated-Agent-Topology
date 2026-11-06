"""memory.usage_probe — Phase 0 of the memory spec (v4): measure how much of the injected L3 is used.

Diagnostics only, no effect on retrieval or prompts. After each turn the bot passes the exact
context blocks the orchestrator injected (``on_context_assembled``) and the final reply; this
splits the L3 block ("[Context recuperat din istoric]") into its entries and, per entry, counts
how many of its distinctive terms also appear in the reply.

"Used" is an APPROXIMATION, not ground truth: term overlap >= MIN_OVERLAP and >= MIN_RATIO of the
entry's distinctive terms. The point is a relative signal (which entries earn their tokens), and
the log line says so (``approx=term-overlap``).
"""
from __future__ import annotations

import re

from memory.budget import estimate_tokens
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["L3_HEADER", "split_l3_entries", "measure", "log_usage"]

L3_HEADER = "[Context recuperat din istoric]"
MIN_OVERLAP = 3
MIN_RATIO = 0.10
_ENTRY_START = re.compile(r"(?m)^- (?=\[)")
_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_STOP = frozenset("""
acest aceasta aceste acesta acolo acum ainte asta astea atunci avea aveam aveau care cand când
cele celor ceva chiar cine cineva cum dacă daca dintre doar după dupa este eram erau eşti ești
fiecare fost foarte făcut iar intre între îmi încă înainte lângă lucru mai mult multe niciun nimic
noastră noastre nostru nouă orice pentru poate până prin putea sale sunt suntem sunteți toate
totul tocmai unde unei unele unor unui vreau vrea vreo zice about after also because been before
being could does doing done each from have having here into just like more most much only other
over same should some such than that their them then there these they this those through under
very what when where which while will with would your yours user assistant
""".split())


def _terms(text: str) -> set[str]:
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(text or "")) if w not in _STOP}


def split_l3_entries(blocks: list[str]) -> list[str]:
    """The L3 entries (one string each) from the injected context blocks; [] when no L3 block."""
    for block in blocks or []:
        if block.startswith(L3_HEADER):
            body = block[len(L3_HEADER):].lstrip("\n")
            return [e.strip() for e in _ENTRY_START.split(body) if e.strip()]
    return []


def measure(blocks: list[str], reply: str) -> dict:
    reply_terms = _terms(reply)
    rows = []
    for entry in split_l3_entries(blocks):
        terms = _terms(entry)
        hit = len(terms & reply_terms)
        used = hit >= MIN_OVERLAP and bool(terms) and hit / len(terms) >= MIN_RATIO
        rows.append({"tokens": estimate_tokens(entry), "terms": len(terms), "overlap": hit, "used": used,
                     "head": re.sub(r"\s+", " ", entry)[:48]})
    injected = sum(r["tokens"] for r in rows)
    wasted = sum(r["tokens"] for r in rows if not r["used"])
    return {"injected": len(rows), "used": sum(r["used"] for r in rows), "l3_tokens": injected,
            "unused_tokens": wasted, "entries": rows}


def log_usage(chat_id: str, blocks: list[str] | None, reply: str) -> dict | None:
    """Log one ``l3_usage`` line for this turn; never raises (diagnostics must not cost the reply)."""
    try:
        if not blocks:
            return None
        m = measure(blocks, reply)
        log.info(
            "l3_usage chat=%s approx=term-overlap injected=%d used=%d l3_tokens=%d unused_tokens=%d entries=%s",
            chat_id, m["injected"], m["used"], m["l3_tokens"], m["unused_tokens"],
            [(r["tokens"], r["overlap"], r["used"], r["head"]) for r in m["entries"]],
        )
        return m
    except Exception:  # noqa: BLE001
        log.debug("l3_usage measurement failed", exc_info=True)
        return None
