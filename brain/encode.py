"""Encoding (brain as a brain, task 3): the understanding of a finished turn is written into the memory graph.

The episode node, a ``despre`` link to each thing it is about, the typed links to earlier episodes (directed
episode -> ``to``), a meta row and the open item it opens or closes. Idempotent; nothing is ever deleted: an open
item that gets closed keeps its row and only receives ``closed_ts`` / ``closed_by``.
"""
from __future__ import annotations

__all__ = ["encode"]

from brain import graph

_LINK_KINDS = {"continuă", "cauză", "urmare", "corectează", "contrazice"}


def encode(db, chat_id: str, lane: str, episode_id: str, ts: float, u: dict, now: float) -> dict:
    """Write turn understanding ``u`` (see brain/understand.py) for ``episode_id``. Returns what was written:
    ``{"things", "links", "opened", "closed"}`` (things linked, typed links touched, 0/1, items newly closed)."""
    out = {"things": 0, "links": 0, "opened": 0, "closed": 0}
    db.execute("INSERT OR IGNORE INTO graph_nodes (id, kind, label, chat_id, topic_id, seq, ts, created_at) "
               "VALUES (?, 'episode', ?, ?, NULL, NULL, ?, ?)", (episode_id, episode_id, chat_id, ts, now))
    seen: set[str] = set()
    for label in u.get("about") or []:
        key = graph.norm_thing(str(label), "project")
        if key is None or key in seen:
            continue
        seen.add(key)
        db.execute("INSERT OR IGNORE INTO graph_nodes (id, kind, label, created_at) VALUES (?, 'thing', ?, ?)",
                   (f"thing:{key}", key, now))
        graph._edge(db, episode_id, f"thing:{key}", "despre", ts or now)  # same structural link ingest writes
        out["things"] += 1
    for link in u.get("links") or []:
        to, kind = str((link or {}).get("to") or ""), (link or {}).get("kind")
        if to and kind in _LINK_KINDS and to != episode_id:
            graph.touch(db, episode_id, to, kind, 0.0, now, initial=0.5)  # a re-encode is not a new strengthening
            out["links"] += 1
    db.execute("INSERT INTO episode_meta (id, type, importance, gist, encoded_at) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(id) DO UPDATE SET type = excluded.type, importance = excluded.importance, "
               "gist = excluded.gist, encoded_at = excluded.encoded_at",
               (episode_id, u.get("type"), float(u.get("importance") or 0.0), u.get("gist") or "", now))
    opens = u.get("opens")
    if opens and opens.get("text"):
        out["opened"] = db.execute("INSERT OR IGNORE INTO open_items (id, chat_id, lane, kind, text, ts) "
                                   "VALUES (?, ?, ?, ?, ?, ?)",
                                   (f"open:{episode_id}", chat_id, lane, opens.get("kind"), opens["text"], ts)).rowcount
    for item_id in u.get("closes") or []:
        out["closed"] += db.execute("UPDATE open_items SET closed_ts = ?, closed_by = ? WHERE id = ? "
                                    "AND closed_ts IS NULL", (now, episode_id, str(item_id))).rowcount
    db.commit()
    return out
