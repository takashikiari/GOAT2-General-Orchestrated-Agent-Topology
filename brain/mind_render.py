"""brain.mind_render — the ``[În minte]`` block as GOAT reads it (spec 2026-11-21 §2 F1, F7).

What the working memory holds, rendered in Romanian within a token budget: what is known (``Ce știu``, never
trimmed), what happened (each memory with its date, its gist and why it is in mind, the strongest with a raw
excerpt), what is open, what is missing. Pure formatting plus one read of the episodic store; nothing is written.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from memory.budget import estimate_tokens

__all__ = ["day", "clean", "memories", "render"]

_RAW_N, _RAW_MAX, _GIST_MAX = 3, 600, 160
_STAMP = re.compile(r"^\s*\[[^\]]{0,40}\]\s*")  # "[2026-11-14T17:38:23+02:00] " or "[2026-11-14 17:38] "
_ROLE = re.compile(r"^\s*(user|assistant)\s*:\s*", re.I)


def day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return "dată necunoscută"


def clean(text: str) -> str:
    """``text`` without a leading "[timestamp] " and "user:"/"assistant:" role prefix (R32), on one line."""
    text = _STAMP.sub("", text or "", count=1)
    return _ROLE.sub("", text, count=1).replace("\n", " ").strip()


async def memories(episodic, mems: list[dict], hits: dict, meta: dict) -> list[dict]:
    """The memories to show, strongest first: text and date from the search hit or the episodic store, gist from
    episode_meta (else the cleaned text's start); a raw excerpt only when it says more than the gist line: an
    excerpt that starts with the gist line shows only what follows it (R32)."""
    found = {mid: (r.get("content") or "", (r.get("metadata") or {}).get("timestamp")) for mid, r in hits.items()}
    need = [it["item"] for it in mems if it["item"] not in found]
    if need:
        got = await asyncio.to_thread(episodic._get_collection().get, ids=need, include=["documents", "metadatas"])
        for i, d, m in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            found[i] = (d or "", (m or {}).get("timestamp"))
    out = []
    for it in mems:
        text, ts = found.get(it["item"], ("", None))
        gist, node_ts = meta.get(it["item"], ("", None))
        if not (text or gist):
            continue  # a memory the store no longer has: nothing to show
        line = (gist or clean(text)[:_GIST_MAX]).replace("\n", " ").strip()
        raw = text[:_RAW_MAX] if gist or len(text) > _GIST_MAX else ""
        if raw and line and clean(raw).startswith(line):
            rest = clean(raw)[len(line):].strip()
            raw = "…" + rest if rest else ""
        out.append({"id": it["item"], "date": day(ts if ts is not None else node_ts), "why": it["why"],
                    "gist": line, "raw": raw})
    return out


def render(facts: dict, mems: list[dict], opens: list[str], missing: list[str],
           budget: int) -> tuple[str, list[str], dict[str, str]]:
    """The block within ``budget`` tokens. ``mems`` strongest first, ``opens`` most recent first, ``missing`` the
    named things the graph knows nothing about (R27: decided by the graph, never by what was trimmed here).
    Trimmed in this order: memory lines without an excerpt (weakest first), raw excerpts, the remaining memory
    lines, open lines (oldest first), missing lines; ``Ce știu`` never."""
    keep, raw, opens = list(mems), {m["id"] for m in mems[:_RAW_N] if m["raw"]}, list(opens)
    miss_cap = len(missing or [])

    def build() -> str:
        parts = ["[În minte]"]
        if facts:
            parts.append("Ce știu:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()))
        if keep:
            lines = []
            for m in keep:
                lines.append(f"- ({m['date']}) {m['gist']} — {m['why']}")
                if m["id"] in raw:
                    lines.append(f"  „{m['raw']}”")
            parts.append("Ce s-a întâmplat:\n" + "\n".join(lines))
        if opens:
            parts.append("Ce e deschis:\n" + "\n".join(opens))
        if miss_cap:
            parts.append("Ce nu știu:\n" + "\n".join(f"- nu ai nimic despre {x}" for x in missing[:miss_cap]))
        return "\n\n".join(parts) if len(parts) > 1 else ""

    block = build()
    while block and estimate_tokens(block) > budget:
        plain = [m for m in keep if m["id"] not in raw]
        if plain:
            keep.remove(plain[-1])
        elif raw:
            raw.discard(next(m["id"] for m in reversed(keep) if m["id"] in raw))
        elif keep:
            keep.pop()
        elif opens:
            opens.pop()
        elif miss_cap:
            miss_cap -= 1
        else:
            break
        block = build()
    texts = {m["id"]: m["gist"] + ("\n" + m["raw"] if m["id"] in raw else "") for m in keep}
    return block, [m["id"] for m in keep], texts
