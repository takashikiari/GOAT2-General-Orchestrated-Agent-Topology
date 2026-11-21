"""Working memory ("În minte", brain/mind.py): one shared state per chat and lane, lit, carried, read and reinforced."""
from __future__ import annotations

import io
import pathlib
import re
import tokenize

from brain import brain as b
from brain import mind
from brain.store import Brain

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _db(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})._db


def _rows(db):
    return db.execute("SELECT COUNT(*) FROM mind").fetchone()[0]


def test_light_converges_and_caps_at_one(tmp_path):
    db = _db(tmp_path)
    assert mind.light(db, "1", "human", "x", "fapt", 0.6, "search", "caută", 1.0) == 0.6
    assert mind.light(db, "1", "human", "x", "fapt", 0.6, "graph", "graf", 2.0) == 1.0
    mind.light(db, "1", "human", "x", "fapt", 0.1, "graph", "graf", 3.0)
    top = mind.top(db, "1", "human", 5, 0.0)
    assert top[0]["activation"] == 1.0 and top[0]["sources"] == ["search", "graph"]


def test_carry_fades_to_floor_and_keeps_the_row(tmp_path):
    db = _db(tmp_path)
    mind.light(db, "1", "human", "x", "fapt", 1.0, "search", "w", 1.0)
    n = 0
    for _ in range(15):  # 0.6**10 is still 0.006; the floor is reached after 14
        n = mind.carry(db, "1", "human", 0.6, 0.001)
    assert n == 1 and _rows(db) == 1
    assert db.execute("SELECT activation FROM mind").fetchone()[0] == 0.001


def test_top_orders_by_activation_and_filters_by_lane(tmp_path):
    db = _db(tmp_path)
    mind.light(db, "1", "human", "a", "fapt", 0.3, "s", "wa", 1.0)
    mind.light(db, "1", "human", "b", "amintire", 0.9, "s", "wb", 1.0)
    mind.light(db, "1", "human", "c", "deschis", 0.01, "s", "wc", 1.0)
    mind.light(db, "1", "channel", "d", "fapt", 0.8, "s", "wd", 1.0)
    top = mind.top(db, "1", "human", 10, 0.05)
    assert [t["item"] for t in top] == ["b", "a"]
    assert {"item", "kind", "activation", "sources", "why"} <= set(top[0])
    assert [t["item"] for t in mind.top(db, "1", "human", 1, 0.05)] == ["b"]


def test_mark_reinforces_used_and_fades_present(tmp_path):
    db = _db(tmp_path)
    for i in "uvw":
        mind.light(db, "1", "human", i, "fapt", 0.5, "s", "w", 1.0)
    mind.mark(db, "1", "human", {"u": True, "v": False}, 9.0)
    act = {r[0]: r[1] for r in db.execute("SELECT item, activation FROM mind")}
    assert abs(act["u"] - 0.7) < 1e-9 and abs(act["v"] - 0.4) < 1e-9 and act["w"] == 0.5
    assert db.execute("SELECT last_used FROM mind WHERE item='u'").fetchone()[0] == 9.0


def _strip(src: str) -> str:
    out = []
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT or (t.type == tokenize.STRING and t.string.lstrip("rbfRBF")[:3] in ('"""', "'''")):
            continue
        out.append(t.string)
    return " ".join(out)


def test_no_delete_in_brain_code():
    for p in (_ROOT / "brain").glob("*.py"):
        assert not re.search(r"\b(DELETE|DROP|TRUNCATE)\b", _strip(p.read_text()), re.I), p.name


def test_mind_off_by_default(monkeypatch):
    """The code default is off (config/brain.toml switches it on); with brain off, mind is off whatever [mind] says."""
    d = b._MIND_DEFAULTS
    assert d["enabled"] is False and d["carry"] == 0.6 and d["floor"] == 0.001
    assert d["expose_items"] == 12 and d["encode_per_tick"] == 20
    monkeypatch.setitem(b.MIND, "enabled", False)
    assert b.mind_on() is False
