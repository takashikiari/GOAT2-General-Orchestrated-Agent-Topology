"""brain/mind_sleep.py: the L3 history is encoded oldest first, slice by slice, through the same understanding."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from brain import brain as b
from brain import graph as g
from brain import mind_sleep as ms
from brain.store import Brain


def _brain(tmp_path):
    return Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def get(self, ids, include=None):
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


def test_backfill_encodes_oldest_first_and_links_within_topic(tmp_path):
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = _docs()
        docs["e3"] = "user: Mesaje noi pe canalul agent-coord x\nassistant: ok"
        _seed(brain, docs)
        client = _Client()
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 2)) == 2
        done = {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")}
        assert done == {"e1", "e2"}
        assert "salut 1" in client.prompts[0] and "răspuns 1" in client.prompts[0]
        assert "e1" in client.prompts[1]  # the previous episode of the topic was in mind
        assert brain._db.execute("SELECT count(*) FROM open_items").fetchone()[0] == 0  # R3: old history
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 5)) == 1
        assert brain._db.execute("SELECT 1 FROM episode_meta WHERE id='e3'").fetchone()
    finally:
        brain.close()


def test_backfill_skips_encoded_and_retries_failed_later(tmp_path):
    brain = _brain(tmp_path)
    ms._failed.clear()
    try:
        docs = _docs()
        _seed(brain, docs)
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(fail=("salut 1",)), 1)) == 0  # e1 fails
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(fail=("salut 1",)), 1)) == 1  # e1 skipped
        assert {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")} == {"e2"}
        ms._failed["e1"] = time.time() - 3601  # an hour later it is retried, oldest first
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 1)) == 1
        assert {r[0] for r in brain._db.execute("SELECT id FROM episode_meta")} == {"e1", "e2"}
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Client(), 5)) == 1  # only e3 left; e1/e2 not redone
    finally:
        ms._failed.clear()
        brain.close()
