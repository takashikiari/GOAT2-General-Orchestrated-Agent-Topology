"""brain.recall — activation over the living memory graph (design §4 and §12). Pure reads; the touches that
follow a turn are written by brain/live.py.

What is in mind right now (the memories retrieval brought, the episodes of the current conversation) lights up; the
activation flows along the links at most three steps, halving at each step. A link conducts by its CURRENT weight
(faded to now, §12.4), but never less than 10%: a link faded to the 0.001 floor still lets a strong stimulus reach
its neighbour (Laris: nothing disappears, it only fades until a stimulus brings it back), while links lived often
dominate. Three steps, so a memory reaches another through two associated things (episode → thing → thing it was
met with → episode) — the association, not the line. The line of the conversation ('urmează') conducts only 30%: the
recent turns are already in the prompt. A thing passes each link a share divided by its number of links (an episode
by the square root), so hubs ("laris", "lenovo") do not drag everything along.

A thing is read as its whole group of spellings ("wave 2" + "wave2" + "wave-2", brain/graph_alias.py): its links
are the group's links and a neighbour spelled differently counts as the group's principal. A generic "thing"
("aici", "super") conducts nothing.

The result: the most activated episodes that are not already in mind, each with the path that reached it (to show
GOAT why it remembers, and to touch those links after the turn).
"""
from __future__ import annotations

import math

from brain.graph import faded
from brain.graph_alias import canon, generic, group

__all__ = ["spread"]

_HOP_DECAY = 0.5
_MIN_CONDUCT = 0.1  # a faded link still conducts this much
_FANOUT = 40  # links read per node per step (the currently strongest first)
_KIND_CONDUCT = {"urmează": 0.3}  # the line of the conversation: already in the prompt, conducts less


def _links(db, nodes: list[str], now: float, half_life_days: float) -> list[tuple[str, str, str, str, float]]:
    """(neighbour, src, dst, kind, current weight) for every link of ``nodes`` (one node or a thing's spellings),
    strongest (as stored) first."""
    q = ",".join("?" * len(nodes))
    rows = db.execute(
        f"SELECT dst, src, dst, kind, weight, coalesce(w_at, last_used, created_at) FROM graph_edges WHERE src IN ({q}) "
        f"UNION ALL SELECT src, src, dst, kind, weight, coalesce(w_at, last_used, created_at) FROM graph_edges "
        f"WHERE dst IN ({q}) ORDER BY 5 DESC LIMIT ?", (*nodes, *nodes, _FANOUT)).fetchall()
    return [(o, s, d, k, faded(w, t, now, half_life_days)) for o, s, d, k, w, t in rows]


def _degree(db, nodes: list[str]) -> int:
    q = ",".join("?" * len(nodes))
    return db.execute(f"SELECT (SELECT count(*) FROM graph_edges WHERE src IN ({q})) + (SELECT count(*) FROM "
                      f"graph_edges WHERE dst IN ({q}))", (*nodes, *nodes)).fetchone()[0] or 1


def spread(db, seeds: dict[str, float], now: float, limit: int = 3, hops: int = 3,
           half_life_days: float = 14.0) -> list[dict]:
    """The ``limit`` most activated episodes reached from ``seeds`` ({node: activation}), seeds excluded:
    [{"id", "activation", "path": [(src, dst, kind), ...], "via": <label of the first thing on the path or ''>}]."""
    act = dict(seeds)
    path: dict[str, list[tuple[str, str, str]]] = {k: [] for k in seeds}
    frontier = dict(seeds)
    canon_of: dict[str, str] = {}  # one lookup per node per recall, not one per link

    def _canon(x: str) -> str:
        if x not in canon_of:
            canon_of[x] = canon(db, x)
        return canon_of[x]
    for _ in range(hops):
        nxt: dict[str, float] = {}
        for node, a in frontier.items():
            if generic(node):
                continue
            members = group(db, node)
            links = _links(db, members, now, half_life_days)
            if not links:
                continue
            n = _degree(db, members) if node.startswith("thing:") else len(links)
            share = a * _HOP_DECAY / (n if node.startswith("thing:") else math.sqrt(n))
            for other, src, dst, kind, w in links:
                other = _canon(other)
                if other == node or generic(other):
                    continue
                gain = share * (_MIN_CONDUCT + (1.0 - _MIN_CONDUCT) * w) * _KIND_CONDUCT.get(kind, 1.0)
                if gain > nxt.get(other, 0.0):
                    nxt[other] = gain
                    if other not in seeds and gain > act.get(other, 0.0):
                        path[other] = path.get(node, []) + [(src, dst, kind)]
        for k, v in nxt.items():
            act[k] = act.get(k, 0.0) + v
        frontier = nxt
    out = []
    for node, a in sorted(act.items(), key=lambda x: -x[1]):
        if node in seeds or node.startswith("thing:"):
            continue
        things = [n for e in path.get(node, []) for n in e[:2] if n.startswith("thing:")]
        out.append({"id": node, "activation": round(a, 6), "path": path.get(node, []),
                    "via": things[0][6:] if things else ""})
        if len(out) >= int(limit):
            break
    return out
