"""One thing, one node at reading time (brain/graph_alias.py, design §6): spellings of the same thing join a group
without losing a row, activation follows the whole group, and words that name nothing conduct no activation."""
from __future__ import annotations

import pathlib
import re
import time

from brain import brain as b
from brain import graph as g
from brain import graph_alias as ga
from brain import recall
from brain.store import Brain

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_T = "technology"


def _db(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})._db


def _doc(i, ents, ts):
    return {"id": f"m{i}", "ts": ts, "chat_id": "42", "topic_id": f"t{i}", "sequence_number": 1,
            "entities": ents, "entity_types": ",".join([_T] * len(ents.split(",")))}


def _counts(db):
    return (db.execute("SELECT count(*) FROM graph_nodes").fetchone()[0],
            db.execute("SELECT count(*) FROM graph_edges").fetchone()[0])


def test_no_delete_in_the_alias_and_recall_code():
    for name in ("brain/graph_alias.py", "brain/recall.py"):
        src = (_ROOT / name).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        assert not re.search(r"\bDELETE\b|\bDROP\b|\bTRUNCATE\b", code, re.I), name


def test_spellings_join_the_most_talked_about_one_and_nothing_is_lost(tmp_path):
    db, t0 = _db(tmp_path), time.time() - 3600
    g.ingest(db, [_doc(1, "wave 2", t0), _doc(2, "wave 2", t0 + 1), _doc(3, "wave2", t0 + 2),
                  _doc(4, "Wave-2", t0 + 3), _doc(5, "gică", t0 + 4), _doc(6, "gica", t0 + 5),
                  _doc(7, "activation", t0 + 6), _doc(8, "activation.py", t0 + 7)], t0 + 10)
    before = _counts(db)
    assert ga.unify(db) == 3                                 # wave2 + wave-2 -> "wave 2"; one of gică/gica
    assert _counts(db) == before                             # no row lost, no link lost
    assert ga.canon(db, "thing:wave2") == ga.canon(db, "thing:wave-2") == "thing:wave 2"
    assert ga.canon(db, "thing:gica") == ga.canon(db, "thing:gică")
    assert ga.canon(db, "thing:activation.py") == "thing:activation.py"      # different things stay apart
    assert sorted(ga.group(db, "thing:wave 2")) == ["thing:wave 2", "thing:wave-2", "thing:wave2"]
    assert ga.unify(db) == 0                                 # idempotent: the next tick changes nothing


def test_the_principal_stays_put_when_a_variant_later_has_more_episodes(tmp_path):
    db, t0 = _db(tmp_path), time.time() - 3600
    g.ingest(db, [_doc(1, "wave 2", t0), _doc(2, "wave 2", t0 + 1), _doc(3, "wave2", t0 + 2)], t0 + 5)
    ga.unify(db)
    g.ingest(db, [_doc(i, "wave2", t0 + 10 + i) for i in range(4, 9)], t0 + 30)
    assert ga.unify(db) == 0 and ga.canon(db, "thing:wave2") == "thing:wave 2"


def test_activation_through_one_spelling_reaches_the_memories_of_the_others(tmp_path):
    db, t0 = _db(tmp_path), time.time() - 3600
    g.ingest(db, [_doc(1, "wave2", t0), _doc(2, "wave 2", t0 + 1)], t0 + 5)
    reach = lambda: {c["id"] for c in recall.spread(db, {"m1": 1.0}, time.time(), limit=5)}  # noqa: E731
    assert "m2" not in reach()                               # split spellings: no association
    ga.unify(db)
    assert "m2" in reach()                                   # one thing: "wave2" brings the "wave 2" memory


def test_words_that_name_nothing_conduct_no_activation_and_form_no_pairs(tmp_path):
    db, t0 = _db(tmp_path), time.time() - 3600
    g.ingest(db, [_doc(1, "aici,redis", t0), _doc(2, "aici", t0 + 1), _doc(3, "redis", t0 + 2)], t0 + 5)
    got = {c["id"] for c in recall.spread(db, {"m1": 1.0}, time.time(), limit=5)}
    assert "m3" in got and "m2" not in got                   # redis associates, "aici" does not
    g.pairs_slice(db, hub_fraction=1.0)
    assert db.execute("SELECT count(*) FROM graph_edges WHERE kind = 'împreună'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM graph_nodes WHERE id = 'thing:aici'").fetchone()[0] == 1   # still there


def test_spellings_of_one_thing_never_pair_with_each_other(tmp_path):
    db, t0 = _db(tmp_path), time.time() - 3600
    g.ingest(db, [_doc(1, "wave 2", t0), _doc(2, "wave2,wave 2,redis", t0 + 1)], t0 + 5)
    ga.unify(db)
    g.pairs_slice(db, hub_fraction=1.0)
    pairs = db.execute("SELECT src, dst FROM graph_edges WHERE kind = 'împreună'").fetchall()
    assert pairs == [("thing:redis", "thing:wave 2")]
