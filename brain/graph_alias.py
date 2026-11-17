"""brain.graph_alias — one thing, one node at reading time (design §6 'Unificare', Laris 2026-11-17).

The same thing reaches the graph under several spellings: "wave 2" / "wave2" / "wave-2", "petvoyageuk" /
"petvoyage uk", "auto_promote" / "autopromote", "gică" / "gica" (live graph 2026-11-17: 106 such groups, 3284
episode links split between them). ``unify`` marks every variant ``alias_of`` the spelling with the most episodes;
nothing is deleted or moved: the variant keeps its row and its links, and reading follows the group (``group``,
``canon``), so activation through "wave2" reaches the episodes about "wave 2" too. Only spelling differences merge
(case, spaces, hyphens, underscores, dots, slashes, diacritics); "activation" and "activation.py" stay apart.

Some "things" name nothing: GLiNER tags "aici", "unde", "super", "salut", "lumea" as entities. A thing passes its
activation divided by its number of links, so a word met in 3 unrelated turns would tie them strongly. ``generic``
things conduct no activation and form no pairs; their rows and links stay (Laris: nothing is ever deleted).
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["GENERIC", "thing_key", "generic", "canon", "group", "unify"]

# words seen as thing nodes in the live graph (2026-11-17) that name nothing stable: greetings, adverbs, adjectives,
# verbs and nouns too general to associate two memories. Technical words (push, merge, restart, commit) stay things.
GENERIC = frozenset("""
aici unde super perfect măi mai șefule sefule neața neata salut bun critic omul lumea prezent prefer preferi preferă
prefera preferat preferințe preferinte alegerea automat mecanism local home working user client clienții clientii
proiect project canal channel model live cold warm app system store web cloud internet sesiune companie context
echipa echipă echipei start post reset static write watch request process location events
""".split())
_SEP = re.compile(r"[\s\-_./]+")


def thing_key(label: str) -> str:
    """'Wave-2' / 'wave 2' / 'wave2' -> 'wave2'; 'gică' -> 'gica'."""
    s = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode().lower()
    return _SEP.sub("", s)


def generic(node: str) -> bool:
    """A thing node (``thing:<label>``) that names nothing and conducts no activation."""
    return node.startswith("thing:") and node[6:] in GENERIC


def canon(db, node: str) -> str:
    """The node that stands for ``node``'s group: its ``alias_of``, or itself."""
    if not node.startswith("thing:"):
        return node
    row = db.execute("SELECT alias_of FROM graph_nodes WHERE id = ?", (node,)).fetchone()
    return row[0] if row and row[0] else node


def group(db, node: str) -> list[str]:
    """``node`` and every spelling marked as an alias of it (a principal node first)."""
    if not node.startswith("thing:"):
        return [node]
    return [node] + [r[0] for r in db.execute("SELECT id FROM graph_nodes WHERE alias_of = ?", (node,))]


def unify(db) -> int:
    """Mark spelling variants ``alias_of`` their group's principal: the spelling already principal for others,
    else the one with the most episodes (then the shortest, then alphabetical). UPDATE only. Returns how many
    rows changed (0 when the graph is already unified)."""
    rows = db.execute("SELECT n.id, n.label, n.alias_of, (SELECT count(*) FROM graph_edges e WHERE e.dst = n.id AND "
                      "e.kind = 'despre') FROM graph_nodes n WHERE n.kind = 'thing'").fetchall()
    groups: dict[str, list[tuple]] = {}
    for r in rows:
        groups.setdefault(thing_key(r[1]), []).append(r)
    principals = {r[2] for r in rows if r[2]}
    changed = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        held = [m for m in members if m[0] in principals and not m[2]]
        top = held[0] if held else sorted(members, key=lambda m: (-m[3], len(m[1]), m[1]))[0]
        for m in members:
            want = None if m[0] == top[0] else top[0]
            if m[2] != want:
                db.execute("UPDATE graph_nodes SET alias_of = ? WHERE id = ?", (want, m[0]))
                changed += 1
    db.commit()
    return changed
