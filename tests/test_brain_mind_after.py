"""brain/mind_live.py after_turn: what the reply used stays lit and strengthens its links; the turn is understood
and encoded into the graph; a failed understanding leaves the turn unencoded for the backfill."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from brain import brain as b
from brain import mind
from brain import mind_live as ml

TEXT_A = "Laris a ales PostgreSQL pentru baza de date a proiectului PetVoyageUK deoarece este stabil"
TEXT_B = "Pisica vecinului doarme pe balcon dimineața devreme"
REPLY = "Am ales PostgreSQL pentru baza de date a proiectului PetVoyageUK, este stabil."


class FakeClient:
    def __init__(self, payload=None, exc=None):
        self.calls = []
        self._payload, self._exc = payload, exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self._payload)),
                                                        finish_reason="stop")])


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "m.sqlite")})
    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": True})
    monkeypatch.setattr(b, "_instance", None)
    yield b.get_instance()
    if b._instance is not None:
        b._instance.close()


def _lit(brain):
    now = time.time()
    mind.light(brain._db, "42", "human", "a", "amintire", 0.5, "căutare", "x", now)
    mind.light(brain._db, "42", "human", "b", "amintire", 0.5, "căutare", "x", now)
    brain._db.commit()


def _run(client):
    asyncio.run(ml.after_turn(client, "42", "human", "t1", "Ce bază de date am ales?", REPLY, ["a", "b"],
                              {"a": TEXT_A, "b": TEXT_B}))


def _act(brain, item):
    return brain._db.execute("SELECT activation FROM mind WHERE item = ?", (item,)).fetchone()[0]


def _weight(brain, x):
    return brain._db.execute("SELECT weight FROM graph_edges WHERE kind = 'gândite_împreună' AND "
                             "((src = 't1' AND dst = ?) OR (src = ? AND dst = 't1'))", (x, x)).fetchone()[0]


def test_used_memories_stay_lit_and_link_to_the_turn(on):
    _lit(on)
    _run(FakeClient(exc=RuntimeError("down")))
    assert _act(on, "a") == pytest.approx(0.7) and _act(on, "b") == pytest.approx(0.4)
    assert _weight(on, "a") == pytest.approx(0.2) and _weight(on, "b") == pytest.approx(0.05)


def test_turn_is_encoded_with_links_to_what_was_in_mind(on):
    _lit(on)
    payload = {"type": "decizie", "importance": 0.8, "gist": "Alege PostgreSQL", "about": ["PostgreSQL"],
               "links": [{"to": "a", "kind": "continuă"}], "opens": None, "closes": []}
    client = FakeClient(payload)
    _run(client)
    assert len(client.calls) == 1
    assert on._db.execute("SELECT type, gist FROM episode_meta WHERE id = 't1'").fetchone() == \
        ("decizie", "Alege PostgreSQL")
    assert on._db.execute("SELECT 1 FROM graph_edges WHERE src = 't1' AND dst = 'a' AND kind = 'continuă'").fetchone()
    assert on._db.execute("SELECT 1 FROM graph_edges WHERE src = 't1' AND dst = 'thing:postgresql' "
                          "AND kind = 'despre'").fetchone()


def test_failed_understanding_leaves_turn_for_backfill(on):
    _lit(on)
    _run(FakeClient(exc=RuntimeError("down")))
    assert on._db.execute("SELECT 1 FROM episode_meta WHERE id = 't1'").fetchone() is None
    assert _act(on, "a") == pytest.approx(0.7)  # the learning still happened


def _encode_meta(brain, eid, gist, **kw):
    from brain import encode as enc
    u = {"type": "eveniment", "importance": 0.5, "gist": gist, "about": [], "links": [], "opens": None, "closes": []}
    u.update(kw)
    enc.encode(brain._db, "42", "human", eid, time.time(), u, time.time())


def test_open_item_text_reaches_the_model_and_can_be_closed_live(on):
    _encode_meta(on, "p1", "Promit raportul.", opens={"kind": "promisiune", "text": "Trimit raportul Kent"})
    mind.light(on._db, "42", "human", "open:p1", "deschis", 1.0, "deschis", "promisiune", time.time())
    on._db.commit()
    payload = {"type": "eveniment", "importance": 0.5, "gist": "Raport trimis", "about": [], "links": [],
               "opens": None, "closes": ["open:p1"]}
    client = FakeClient(payload)
    _run(client)
    assert "Trimit raportul Kent" in client.calls[0]["messages"][-1]["content"]
    row = on._db.execute("SELECT closed_ts, closed_by FROM open_items WHERE id = 'open:p1'").fetchone()
    assert row[0] is not None and row[1] == "t1"


def test_carried_memory_not_shown_reaches_the_model_with_its_gist(on):
    _encode_meta(on, "c1", "Gist despre cameleoni")
    mind.light(on._db, "42", "human", "c1", "amintire", 0.5, "căutare", "x", time.time())
    on._db.commit()
    client = FakeClient({"type": "eveniment"})
    _run(client)
    assert "Gist despre cameleoni" in client.calls[0]["messages"][-1]["content"]


def test_expose_message_understanding_sees_carried_gist_and_open_text(on):
    _encode_meta(on, "c1", "Gist despre cameleoni", opens={"kind": "promisiune", "text": "Trimit raportul Kent"})
    now = time.time()
    mind.light(on._db, "42", "human", "c1", "amintire", 1.0, "căutare", "x", now)
    mind.light(on._db, "42", "human", "open:c1", "deschis", 1.0, "deschis", "promisiune", now)
    on._db.commit()
    client = FakeClient({"about": [], "asks_past": False})
    asyncio.run(ml.expose(client, None, "42", "human", "salut", [], {}, 4000))
    prompt = client.calls[0]["messages"][-1]["content"]
    assert "Gist despre cameleoni" in prompt and "Trimit raportul Kent" in prompt
