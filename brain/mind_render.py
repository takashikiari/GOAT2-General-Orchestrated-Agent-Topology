"""brain.mind_render — the ``[În minte]`` block as GOAT reads it (spec 2026-11-21 §2 F1, F7).

What the working memory holds, rendered in Romanian within a token budget: the rules (``Reguli``) and what is
known (``Ce știu``), never trimmed, what happened (each memory with its date, its gist and why it is in mind, the
strongest with a raw excerpt), what is open, what is missing. Pure formatting plus one read of the episodic store; nothing is written.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from brain.mind_text import digest_body, strip_markup
from memory.budget import estimate_tokens

__all__ = ["day", "clean", "memories", "render"]

_RAW_N, _RAW_MAX, _GIST_MAX = 3, 600, 160
_MEANINGFUL = 20  # a memory that was tool-call markup and has fewer characters left than this is not shown (Q6)
_STAMP = re.compile(r"^\s*\[[^\]]{0,40}\]\s*")  # "[2026-11-14T17:38:23+02:00] " or "[2026-11-14 17:38] "
_ROLE = re.compile(r"^[ \t]*(user|assistant)[ \t]*:[ \t]*", re.I | re.M)  # at any line start


def day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return "dată necunoscută"


def clean(text: str) -> str:
    """``text`` without a leading "[timestamp] " and without the "user:"/"assistant:" role prefix of every line
    (R32), on one line."""
    text = _STAMP.sub("", text or "", count=1)
    return _ROLE.sub("", text).replace("\n", " ").strip()


async def memories(episodic, mems: list[dict], hits: dict, meta: dict) -> list[dict]:
    """The memories to show, strongest first: text and date from the search hit or the episodic store, gist from
    episode_meta (else the cleaned text's start); a raw excerpt only when it says more than the gist line: an
    excerpt that starts with the gist line shows only what follows it (R32). A digest shows its messages, not its
    fixed header and footer; tool-call markup is left out and a memory that was mostly markup (under _MEANINGFUL
    characters left, gist included) is not shown (Q6); the same content under several ids (the WHOLE cleaned text, lowercased, whitespace
    collapsed: a true repeat, Q1) is shown once, the most active (P2)."""
    found = {mid: (r.get("content") or "", (r.get("metadata") or {}).get("timestamp")) for mid, r in hits.items()}
    need = [it["item"] for it in mems if it["item"] not in found]
    if need:
        got = await asyncio.to_thread(episodic._get_collection().get, ids=need, include=["documents", "metadatas"])
        for i, d, m in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            found[i] = (d or "", (m or {}).get("timestamp"))
    out, seen = [], set()
    for it in mems:
        text, ts = found.get(it["item"], ("", None))
        gist, node_ts, *rest = meta.get(it["item"], ("", None))
        text = digest_body(text)
        if (bare := strip_markup(text)) != text:
            text = bare
            if max(len(clean(text)), len((gist or "").strip())) < _MEANINGFUL:
                continue  # Q6: a tool-call turn, nothing that happened
        if not (clean(text) or (gist or "").strip()):
            continue  # nothing to show: the store no longer has it, or a digest is empty without its header/footer
        same = " ".join(clean(text or gist).lower().split())
        if same in seen:
            continue  # P2: the same content under another id (a repeated digest): the more active one is shown
        seen.add(same)
        line = (gist or clean(text)[:_GIST_MAX]).replace("\n", " ").strip()
        raw = text[:_RAW_MAX] if gist or len(text) > _GIST_MAX else ""
        if raw and line and clean(raw).startswith(line):
            rest = clean(raw)[len(line):].strip()
            raw = "…" + rest if rest else ""
        out.append({"id": it["item"], "date": day(ts if ts is not None else node_ts), "why": it["why"],
                    "gist": line, "raw": raw, "replaces": rest[0] if rest else ""})
    return out


def render(facts: dict, mems: list[dict], opens: list[str], missing: list[str],
           budget: int, rules: dict | None = None) -> tuple[str, list[str], dict[str, str]]:
    """The block within ``budget`` tokens. ``rules`` the L1 rules (``Reguli``, every turn), ``facts`` the knowledge
    facts in mind (``Ce știu``), ``mems`` strongest first, ``opens`` most recent first, ``missing`` the named things
    the graph knows nothing about (R27: decided by the graph, never by what was trimmed here).
    Trimmed in this order: memory lines without an excerpt (weakest first), raw excerpts, the remaining memory
    lines, open lines (oldest first), missing lines; ``Reguli`` and ``Ce știu`` never."""
    keep, raw, opens = list(mems), {m["id"] for m in mems[:_RAW_N] if m["raw"]}, list(opens)
    miss_cap = len(missing or [])

    def build() -> str:
        parts = ["[În minte]"]
        if rules:
            parts.append("Reguli:\n" + "\n".join(f"- {k}: {v}" for k, v in rules.items()))
        if facts:
            parts.append("Ce știu:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()))
        if keep:
            lines = []
            for m in keep:
                rep = f" (înlocuiește: {m['replaces']})" if m.get("replaces") else ""  # F5: the old version
                lines.append(f"- ({m['date']}) {m['gist']}{rep} — {m['why']}")
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
