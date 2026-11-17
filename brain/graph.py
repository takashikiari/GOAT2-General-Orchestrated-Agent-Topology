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

from brain.graph_alias import canon, generic, group

__all__ = ["SCHEMA", "WEIGHT_FLOOR", "KEPT_TYPES", "UNDIRECTED", "norm_thing", "ingest", "counts", "cursor",
           "faded", "touch", "pairs_slice", "fade_slice", "migrate"]

WEIGHT_FLOOR = 0.001  # a link fades to this and stays forever
HALF_LIFE_DAYS = 14.0  # an untouched link halves every 14 days (§12.4)
UNDIRECTED = {"împreună", "gândite_împreună"}  # stored once per pair, ends in alphabetical order
KEPT_TYPES = {"person", "technology", "project", "organization", "location", "event", "preference"}
_FOLLOW_GAP_S = 15 * 60  # no thread id: the next memory of the same chat within this gap continues the thread

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
    alias_of TEXT, chat_id TEXT, topic_id TEXT, seq INTEGER, ts REAL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS graph_nodes_topic ON graph_nodes (topic_id, seq);
CREATE INDEX IF NOT EXISTS graph_nodes_chat_ts ON graph_nodes (chat_id, ts);
CREATE TABLE IF NOT EXISTS graph_edges (src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
    weight REAL NOT NULL, created_at REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0, w_at REAL,
    PRIMARY KEY (src, dst, kind));
CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges (dst, kind);
CREATE INDEX IF NOT EXISTS graph_nodes_alias ON graph_nodes (alias_of);
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


def migrate(db) -> None:
    """A store made before §12 gets the w_at column (when the weight was last brought up to date). No row is lost."""
    if "w_at" not in {r[1] for r in db.execute("PRAGMA table_info(graph_edges)")}:
        db.execute("ALTER TABLE graph_edges ADD COLUMN w_at REAL")
        db.commit()


def faded(weight: float, since: float | None, now: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """The weight brought up to ``now``: halves every ``half_life_days`` without a touch, never below the floor."""
    if since is None:
        return max(WEIGHT_FLOOR, float(weight))
    days = max(0.0, now - float(since)) / 86400.0
    return max(WEIGHT_FLOOR, float(weight) * 0.5 ** (days / half_life_days))


def touch(db, a: str, b: str, kind: str, delta: float, now: float, initial: float | None = None) -> float:
    """A link is touched (§12.3): created at ``initial`` (default ``delta``) the first time; afterwards its weight is
    brought up to date (faded) and strengthened by ``delta`` (max 1), uses + 1, last_used = now. Returns the weight.
    Undirected kinds are stored once per pair. Never deletes anything."""
    if a == b:
        return 0.0
    if kind in UNDIRECTED and b < a:
        a, b = b, a
    row = db.execute("SELECT weight, coalesce(w_at, last_used, created_at) FROM graph_edges WHERE src = ? AND dst = ? "
                     "AND kind = ?", (a, b, kind)).fetchone()
    if row is None:
        w = max(WEIGHT_FLOOR, min(1.0, float(delta if initial is None else initial)))
        db.execute("INSERT INTO graph_edges (src, dst, kind, weight, created_at, last_used, uses, w_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, 1, ?)", (a, b, kind, w, now, now, now))
        return w
    w = min(1.0, faded(row[0], row[1], now) + float(delta))
    db.execute("UPDATE graph_edges SET weight = ?, w_at = ?, last_used = ?, uses = uses + 1 WHERE src = ? AND dst = ? "
               "AND kind = ?", (w, now, now, a, b, kind))
    return w


def _edge(db, src: str, dst: str, kind: str, now: float, weight: float = 1.0) -> None:
    """Insert a structural link (despre / urmează), or keep the existing one as it is. ``now`` is the episode's own
    time, so the link fades from when it was lived."""
    db.execute("INSERT OR IGNORE INTO graph_edges (src, dst, kind, weight, created_at, w_at) VALUES (?, ?, ?, ?, ?, ?)",
               (src, dst, kind, max(WEIGHT_FLOOR, float(weight)), now, now))


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
            _edge(db, eid, tid, "despre", ts or now)
            added["despre"] += db.total_changes - before
        if topic:
            prev = db.execute("SELECT id FROM graph_nodes WHERE kind = 'episode' AND topic_id = ? AND ts < ? AND id != ? "
                              "ORDER BY ts DESC LIMIT 1", (topic, ts, eid)).fetchone()
        else:
            prev = db.execute("SELECT id FROM graph_nodes WHERE kind = 'episode' AND chat_id IS ? AND ts < ? AND ts >= ? "
                              "AND id != ? ORDER BY ts DESC LIMIT 1", (chat, ts, ts - _FOLLOW_GAP_S, eid)).fetchone()
        if prev:
            before = db.total_changes
            _edge(db, prev[0], eid, "urmează", ts or now)
            added["urmeaza"] += db.total_changes - before
    db.commit()
    return added


def pairs_slice(db, limit: int = 200, hub_fraction: float = 0.15) -> dict:
    """§12.2: things met in the same episode get linked directly ('împreună'), oldest episodes first, at the
    episode's own time (0.1 the first time, +0.1 at every new meeting). A thing present in more than
    ``hub_fraction`` of all episodes forms no pairs. Its own cursor ('pairs'); returns what it did."""
    since = cursor(db, "pairs")
    eps = db.execute("SELECT id, ts FROM graph_nodes WHERE kind = 'episode' AND ts > ? ORDER BY ts LIMIT ?",
                     (since, int(limit))).fetchall()
    if not eps:
        return {"episodes": 0, "pairs": 0}
    total = db.execute("SELECT count(*) FROM graph_nodes WHERE kind = 'episode'").fetchone()[0] or 1
    pairs = 0
    for eid, ts in eps:
        things = sorted({canon(db, r[0]) for r in db.execute("SELECT dst FROM graph_edges WHERE src = ? AND "
                                                            "kind = 'despre'", (eid,))})
        keep = [t for t in things if not generic(t) and _episodes(db, t) <= hub_fraction * total]
        for i in range(len(keep)):
            for j in range(i + 1, len(keep)):
                touch(db, keep[i], keep[j], "împreună", 0.1, float(ts), initial=0.1)
                pairs += 1
    db.commit()
    cursor(db, "pairs", value=float(eps[-1][1]))
    return {"episodes": len(eps), "pairs": pairs}


def _episodes(db, thing: str) -> int:
    """How many episodes talk about ``thing`` under any of its spellings."""
    members = group(db, thing)
    q = ",".join("?" * len(members))
    return db.execute(f"SELECT count(DISTINCT src) FROM graph_edges WHERE dst IN ({q}) AND kind = 'despre'",
                      members).fetchone()[0]


def fade_slice(db, now: float, limit: int = 500, half_life_days: float = HALF_LIFE_DAYS) -> int:
    """§12.4 in sleep: bring the stalest weights up to date (UPDATE, never DELETE), at most ``limit`` per call,
    only links not brought up to date for a day. Reading computes the same value, so this only keeps the stored
    number honest. Returns how many were updated."""
    rows = db.execute("SELECT src, dst, kind, weight, coalesce(w_at, last_used, created_at) FROM graph_edges "
                      "WHERE coalesce(w_at, last_used, created_at) < ? ORDER BY 5 LIMIT ?",
                      (now - 86400.0, int(limit))).fetchall()
    for src, dst, kind, w, since in rows:
        db.execute("UPDATE graph_edges SET weight = ?, w_at = ? WHERE src = ? AND dst = ? AND kind = ?",
                   (faded(w, since, now, half_life_days), now, src, dst, kind))
    db.commit()
    return len(rows)


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
