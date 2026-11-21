"""brain/mind_sleep.py: the L3 history is encoded oldest first, slice by slice, through the same understanding."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from brain import brain as b
from brain import graph as g
from brain import mind_sleep as ms
from brain.store import Brain


@pytest.fixture(autouse=True)
def _fresh_failures():
    ms._failed.clear()
    ms._fails.clear()
    yield
    ms._failed.clear()
    ms._fails.clear()


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


class _Coll:
    def __init__(self, docs):
        self.docs, self.calls = docs, []

    def get(self, ids, include=None):
        self.calls.append(list(ids))
        ids = [i for i in ids if i in self.docs]
        return {"ids": ids, "documents": [self.docs[i] for i in ids]}


class _Ep:
    def __init__(self, docs):
        self.c = _Coll(docs)

    def _get_collection(self):
        return self.c


class _Client:
    def __init__(self, fail=()):
        self.fail, self.prompts = set(fail), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        p = kw["messages"][-1]["content"]
        self.prompts.append(p)
        if any(f in p for f in self.fail):
            raise RuntimeError("boom")
        body = {"type": "fapt", "importance": 0.5, "gist": "g", "about": ["Prefetch"], "links": [],
                "opens": {"kind": "promisiune", "text": "fac X"}, "closes": []}
        msg = SimpleNamespace(content=json.dumps(body))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def _seed(brain, docs):
    ts0 = time.time() - 10 * 86400
    g.ingest(brain._db, [{"id": i, "ts": ts0 + n * 100, "chat_id": "42", "topic_id": "t1", "sequence_number": n,
                          "entities": "", "entity_types": ""} for n, i in enumerate(docs)], 1.0)


def _docs():
    return {f"e{n}": f"[2026-01-0{n}T10:00:00+02:00] user: salut {n}\nassistant: răspuns {n}" for n in range(1, 4)}


def test_backfill_encodes_newest_first_and_neighbours_have_text(tmp_path):
    """P4: the recent memories first; a neighbour in mind with no gist yet is read by its cleaned raw text, fetched
    in the same Chroma call as the slice."""
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = _docs()
        docs["e3"] = "user: Mesaje noi pe canalul agent-coord x\nassistant: ok"
        _seed(brain, docs)
        client, epi = _Client(), _Ep(docs)
        assert asyncio.run(ms.encode_slice(brain, epi, client, 2)) == 2
        done = {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")}
        assert done == {"e2", "e3"}
        assert "canalul agent-coord" in client.prompts[0]                  # the newest first
        assert "salut 2" in client.prompts[1] and "răspuns 2" in client.prompts[1]
        assert "e1 — amintire — salut 1 răspuns 1" in client.prompts[1]  # raw text, no timestamp/role
        assert len(epi.c.calls) == 1 and set(epi.c.calls[0]) == {"e1", "e2", "e3"}  # one Chroma call
        assert brain._db.execute("SELECT count(*) FROM open_items").fetchone()[0] == 0  # R3: old history
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 5)) == 1
        assert brain._db.execute("SELECT 1 FROM episode_meta WHERE id='e1'").fetchone()
    finally:
        brain.close()


def test_backfill_names_the_user_in_the_understanding(tmp_path):
    brain = _brain(tmp_path)
    try:
        docs = _docs()
        _seed(brain, docs)
        client = _Client()
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 1, user_name="Laris")) == 1
        assert "Cel care îi scrie lui GOAT este Laris" in client.prompts[0]
    finally:
        brain.close()


def test_backfill_skips_encoded_and_retries_failed_later(tmp_path):
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = _docs()
        _seed(brain, docs)
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(fail=("salut 3",)), 1)) == 0  # e3 fails
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(fail=("salut 3",)), 1)) == 1  # e3 skipped
        assert {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")} == {"e2"}
        ms._failed["e3"] = time.time() - 3601  # an hour later it is retried, newest first
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 1)) == 1
        assert {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")} == {"e2", "e3"}
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 5)) == 1  # only e1 left; e2/e3 not redone
    finally:
        ms._failed.clear()
        brain.close()


def test_encode_failure_skips_that_episode_and_continues(tmp_path, monkeypatch):
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = _docs()
        _seed(brain, docs)
        real = ms.encode.encode

        def flaky(db, chat_id, lane, eid, *a):
            if eid == "e1":
                raise RuntimeError("db boom")
            return real(db, chat_id, lane, eid, *a)
        monkeypatch.setattr(ms.encode, "encode", flaky)
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 3)) == 2
        assert {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")} == {"e2", "e3"}
        assert "e1" in ms._failed
    finally:
        ms._failed.clear()
        brain.close()


def _recent_seed(brain, docs):
    ts0 = time.time() - 600
    g.ingest(brain._db, [{"id": i, "ts": ts0 + n, "chat_id": "42", "topic_id": "t1", "sequence_number": n,
                          "entities": "", "entity_types": ""} for n, i in enumerate(docs)], 1.0)


def test_recent_episode_keeps_opens_and_digest_is_lane_channel(tmp_path):
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = {"e1": "[2026-11-21T10:00:00+02:00] user: salut\nassistant: ok",
                "e2": "[2026-11-21T10:01:00+02:00] user: Mesaje noi pe canalul agent-coord x\nassistant: ok"}
        _recent_seed(brain, docs)
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 5)) == 2
        rows = dict(brain._db.execute("SELECT id, lane FROM open_items").fetchall())
        assert rows == {"open:e1": "human", "open:e2": "channel"}
    finally:
        ms._failed.clear()
        brain.close()


def test_split_ignores_assistant_word_inside_user_message():
    assert ms._split("[2026-01-01T10:00:00+02:00] user: vezi assistant: x\nassistant: ok") == ("vezi assistant: x", "ok")
    assert ms._split("assistant: doar") == ("", "doar")


def test_mind_tick_is_none_when_mind_off(monkeypatch):
    from telegram_interface import _brain_sleep as bs
    monkeypatch.setattr(bs._brain, "mind_on", lambda: False)
    assert bs._mind_tick(SimpleNamespace()) is None
    monkeypatch.setattr(bs._brain, "mind_on", lambda: True)
    assert bs._mind_tick(SimpleNamespace()) is not None
