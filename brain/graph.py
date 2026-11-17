"""brain.graph — the memory graph, phase F1: built, read by nothing yet (design: docs/superpowers/specs/
2026-11-17-memory-graph-design.md).

Nodes: ``episode`` (one L3 memory, id = its message_id; no text is copied) and ``thing`` (a person / project /
technology / organization / place / event / preference that episodes talk about, from the GLiNER entities L3 already
stores, cleaned). Edges: ``despre`` (episode → thing) and ``urmează`` (episode → the next one in the same thread).

Laris's absolute condition: NOTHING IS EVER DELETED. There is no DELETE in this module: rows are only inserted or
updated, a weight never goes below ``WEIGHT_FLOOR`` (0.001), merged things keep their row (``alias_of``). A test
asserts that the node and edge counts never decrease.

All functions take the brain's SQLite connection and run inside the brain's lock, in a thread (like brain.store).
"""
from __future__ import annotations

import re

__all__ = ["SCHEMA", "WEIGHT_FLOOR", "KEPT_TYPES", "norm_thing", "ingest", "counts", "cursor"]

WEIGHT_FLOOR = 0.001  # a link fades to this and stays forever
KEPT_TYPES = {"person", "technology", "project", "organization", "location", "event", "preference"}
_FOLLOW_GAP_S = 15 * 60  # no thread id: the next memory of the same chat within this gap continues the thread

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
    alias_of TEXT, chat_id TEXT, topic_id TEXT, seq INTEGER, ts REAL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS graph_nodes_topic ON graph_nodes (topic_id, seq);
CREATE INDEX IF NOT EXISTS graph_nodes_chat_ts ON graph_nodes (chat_id, ts);
CREATE TABLE IF NOT EXISTS graph_edges (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
    weight REAL NOT NULL, created_at REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src, dst, kind));
CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges (dst, kind);
CREATE TABLE IF NOT EXISTS graph_cursor (name TEXT PRIMARY KEY, last_ts REAL NOT NULL);
"""

# words GLiNER tags as "technology" that name nothing stable (seen in the live L3): kept out of the thing nodes
_GENERIC = {"sistem", "sistemul", "contor", "contorul", "global", "scoped", "thematic", "fisier", "fișier", "cod",
            "codul", "test", "testul", "teste", "date", "datele", "memorie", "memoria", "mesaj", "mesajul", "ok",
            "tool", "tools", "unealta", "chat", "conversatie", "conversație"}
_ARTICLE = re.compile(r"(-?ului|-?ul|-?lui|-le|-ii|-a)$")
_TIMEISH = re.compile(r"\d{1,2}[:.]\d{2}|^\d+$|\d{4}-\d{2}|\b(ian|feb|mar|apr|mai|iun|iul|aug|sep|oct|noi|dec)\w*\b", re.I)


def norm_thing(entity: str, etype: str) -> str | None:
    """The stable key of an entity, or None when it is not a thing worth a node (wrong type, a time/date, a
    credential, a generic word, too short). 'Prefetch-ul', 'prefetch-ul', 'prefetch' -> 'prefetch'."""
    if (etype or "").strip().lower() not in KEPT_TYPES:  # credentials, dates and times never become nodes
        return None
    e = (entity or "").strip().strip("_*`'\".,;:()[]").lower()
    if len(e) < 3 or _TIMEISH.search(e):
        return None
    if len(e) > 5:
        e = _ARTICLE.sub("", e) or e
    e = e.strip("-_ ")
    if len(e) < 3 or e in _GENERIC:
        return None
    return e


def _things(meta: dict) -> set[str]:
    ents = [x for x in str(meta.get("entities") or "").split(",")]
    types = [x for x in str(meta.get("entity_types") or "").split(",")]
    out = set()
    for ent, typ in zip(ents, types):  # aligned lists (memory/enrichment.py)
        key = norm_thing(ent, typ)
        if key:
            out.add(key)
    return out


def _edge(db, src: str, dst: str, kind: str, now: float, weight: float = 1.0) -> None:
    """Insert a link, or keep the existing one as it is (re-ingesting never weakens or duplicates a link)."""
    db.execute("INSERT OR IGNORE INTO graph_edges (src, dst, kind, weight, created_at) VALUES (?, ?, ?, ?, ?)",
               (src, dst, kind, max(WEIGHT_FLOOR, float(weight)), now))


def ingest(db, docs: list[dict], now: float) -> dict:
    """Episodes (L3 memories: {id, ts, chat_id, topic_id, sequence_number, entities, entity_types}) into the graph,
    oldest first: their episode node, a ``despre`` link to each cleaned thing, an ``urmează`` link from the previous
    memory of the same thread. Idempotent. Returns counts of what was added."""
    added = {"episodes": 0, "things": 0, "despre": 0, "urmeaza": 0}
    for d in sorted(docs, key=lambda x: float(x.get("ts") or 0)):
        eid = str(d.get("id") or "")
        if not eid:
            continue
        ts, chat, topic = float(d.get("ts") or 0), d.get("chat_id"), d.get("topic_id") or None
        seq = d.get("sequence_number")
        added["episodes"] += db.execute(
            "INSERT OR IGNORE INTO graph_nodes (id, kind, label, chat_id, topic_id, seq, ts, created_at) "
            "VALUES (?, 'episode', ?, ?, ?, ?, ?, ?)", (eid, eid, chat, topic, seq, ts, now)).rowcount
        for thing in _things(d):
            tid = f"thing:{thing}"
            added["things"] += db.execute("INSERT OR IGNORE INTO graph_nodes (id, kind, label, created_at) "
                                          "VALUES (?, 'thing', ?, ?)", (tid, thing, now)).rowcount
            before = db.total_changes
            _edge(db, eid, tid, "despre", now)
            added["despre"] += db.total_changes - before
        if topic:
            prev = db.execute("SELECT id FROM graph_nodes WHERE kind = 'episode' AND topic_id = ? AND ts < ? AND id != ? "
                              "ORDER BY ts DESC LIMIT 1", (topic, ts, eid)).fetchone()
        else:
            prev = db.execute("SELECT id FROM graph_nodes WHERE kind = 'episode' AND chat_id IS ? AND ts < ? AND ts >= ? "
                              "AND id != ? ORDER BY ts DESC LIMIT 1", (chat, ts, ts - _FOLLOW_GAP_S, eid)).fetchone()
        if prev:
            before = db.total_changes
            _edge(db, prev[0], eid, "urmează", now)
            added["urmeaza"] += db.total_changes - before
    db.commit()
    return added


def cursor(db, name: str = "l3", value: float | None = None) -> float:
    """Read (and with ``value`` advance — never move back) the ingestion cursor: the last L3 timestamp ingested."""
    row = db.execute("SELECT last_ts FROM graph_cursor WHERE name = ?", (name,)).fetchone()
    current = float(row[0]) if row else 0.0
    if value is not None and value > current:
        db.execute("INSERT INTO graph_cursor VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET last_ts = excluded.last_ts",
                   (name, float(value)))
        db.commit()
        current = float(value)
    return current


def counts(db) -> dict:
    """What the graph holds (for the never-decreasing check and the logs)."""
    nodes = dict(db.execute("SELECT kind, count(*) FROM graph_nodes GROUP BY kind").fetchall())
    edges = dict(db.execute("SELECT kind, count(*) FROM graph_edges GROUP BY kind").fetchall())
    return {"nodes": nodes, "edges": edges, "total_nodes": sum(nodes.values()), "total_edges": sum(edges.values())}
