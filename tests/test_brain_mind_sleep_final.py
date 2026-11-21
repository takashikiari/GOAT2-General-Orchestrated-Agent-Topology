"""brain/mind_sleep.py and the mind tick after the final review (R25, R28, R33, R34): an episode without a topic sees
the chat's previous turns, an episode that keeps failing stops being retried, the backfill never blocks the sleep."""
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
from tests.test_brain_mind_sleep import _Ep


@pytest.fixture
def brain(tmp_path):
    ms._failed.clear()
    ms._fails.clear()
    br = Brain({**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "brain.sqlite")})
    yield br
    br.close()
    ms._failed.clear()
    ms._fails.clear()


class _Linker:
    """Links to e1 whenever e1 is in mind; fails for the prompts containing ``fail``."""

    def __init__(self, fail=None):
        self.prompts, self.fail = [], fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        p = kw["messages"][-1]["content"]
        self.prompts.append(p)
        if self.fail and self.fail in p:
            raise RuntimeError("boom")
        links = [{"to": "e1", "kind": "continuă"}] if "e1 — amintire" in p else []
        body = {"type": "fapt", "gist": "g", "about": ["Render"], "links": links}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)),
                                                        finish_reason="stop")])


def _seed(brain, n, gap, topic=None, chat="42"):
    ts0 = time.time() - 10 * 86400
    g.ingest(brain._db, [{"id": f"e{i + 1}", "ts": ts0 + i * gap, "chat_id": chat, "topic_id": topic,
                          "sequence_number": i, "entities": "", "entity_types": ""} for i in range(n)], 1.0)
    return {f"e{i + 1}": f"user: salut {i + 1}\nassistant: ok" for i in range(n)}


def test_without_topic_the_previous_turns_of_the_chat_are_in_mind(brain):
    docs = _seed(brain, 2, 300)
    client = _Linker()
    assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 5)) == 2
    assert "e1 — amintire — g" in client.prompts[1]                 # e1's gist, encoded just before
    assert "- render" in client.prompts[1]                         # and the things it is about, to reuse
    assert brain._db.execute("SELECT 1 FROM graph_edges WHERE src = 'e2' AND dst = 'e1' AND kind = 'continuă'"
                             ).fetchone()


def test_without_topic_only_the_last_half_hour_counts(brain):
    docs = _seed(brain, 2, 3600)
    client = _Linker()
    asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 5))
    assert "e1 — amintire" not in client.prompts[1]


def test_an_episode_failing_three_times_is_not_retried_in_this_process(brain):
    docs = _seed(brain, 1, 0)
    client = _Linker(fail="salut 1")
    for _ in range(3):
        assert asyncio.run(ms.encode_slice(brain, _Ep(docs), client, 1)) == 0
        ms._failed["e1"] = time.time() - 3601                     # an hour later
    assert len(client.prompts) == 3 and ms._fails["e1"] == 3
    assert asyncio.run(ms.encode_slice(brain, _Ep(docs), _Linker(), 1)) == 0
    assert "e1" in ms._failed                                       # still remembered as given up, never retried


def test_the_mind_tick_starts_one_backfill_and_returns_at_once(monkeypatch):
    from telegram_interface import _brain_sleep as bs
    monkeypatch.setattr(bs._brain, "mind_on", lambda: True)
    monkeypatch.setattr(bs._brain, "get_instance", lambda: None)
    started, gates = [], []

    async def slow(brain, episodic, client, limit):
        started.append(limit)
        await gates[-1].wait()
        return 0
    monkeypatch.setattr(ms, "encode_slice", slow)
    tick = bs._mind_tick(SimpleNamespace(episodic_memory=None, llm_client=None))

    async def go():
        gates.append(asyncio.Event())
        await asyncio.wait_for(tick(), 0.5)                         # returns while the slice still runs
        await asyncio.sleep(0)
        await asyncio.wait_for(tick(), 0.5)                         # one running: no second backfill
        await asyncio.sleep(0)
        assert len(started) == 1 and not tick.running[0].done()
        gates[-1].set()
        await tick.running[0]
        gates.append(asyncio.Event())
        await tick()                                                # finished: the next tick starts a new one
        await asyncio.sleep(0)
        assert len(started) == 2
        gates[-1].set()
        await tick.running[0]
    asyncio.run(go())
