"""brain/mind_live.py expose: the cues of a message, the search, the graph and the open items light ONE working
memory, shown to GOAT as one Romanian block (what is known, what happened, what is open, what is missing)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace

import pytest

from brain import brain as b
from brain import encode as enc
from brain import mind
from brain import mind_live as ml
from memory.budget import estimate_tokens

DAY = 86400.0
T17 = 1795392000.0  # a fixed moment in Nov 2026


class FakeClient:
    def __init__(self, payload=None, exc=None):
        self.calls = []
        self._payload, self._exc = payload, exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        content = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                        finish_reason="stop")])


class _Col:
    def __init__(self, docs):
        self.docs = docs

    def get(self, ids=None, include=None):
        keep = [i for i in ids if i in self.docs]
        return {"ids": keep, "documents": [self.docs[i][0] for i in keep],
                "metadatas": [{"message_id": i, "timestamp": self.docs[i][1]} for i in keep]}


class FakeEpisodic:
    def __init__(self, docs):
        self.col = _Col(docs)

    def _get_collection(self):
        return self.col


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "CONFIG", {**b._DEFAULTS, "enabled": True, "db_path": str(tmp_path / "mind.sqlite")})
    monkeypatch.setattr(b, "MIND", {**b._MIND_DEFAULTS, "enabled": True})
    monkeypatch.setattr(b, "_instance", None)
    yield b.get_instance()
    if b._instance is not None:
        b._instance.close()


def _hit(mid, score, text, ts=T17):
    return {"content": text, "blended_score": score, "metadata": {"message_id": mid, "timestamp": ts}}


def _encode(brain, eid, ts, **kw):
    u = {"type": "eveniment", "importance": 0.5, "gist": "", "about": [], "links": [], "opens": None, "closes": []}
    u.update(kw)
    enc.encode(brain._db, "42", "human", eid, ts, u, time.time())


def _expose(client, epi, message, hits, facts=None, budget=4000, chat="42"):
    return asyncio.run(ml.expose(client, epi, chat, "human", message, hits, facts or {}, budget))


def _amintiri(block):
    part = block.split("Ce s-a întâmplat:\n", 1)[1].split("\n\n", 1)[0]
    return [ln for ln in part.splitlines() if ln.startswith("- (")]


def test_association_reaches_the_prompt_without_text_similarity(on):
    _encode(on, "e0", T17 - 3 * DAY, gist="Prețul pentru Kent a fost 120 de lire.")
    _encode(on, "e1", T17 - DAY, gist="Laris a corectat prețul Kent la 140.", about=["Render"],
            links=[{"to": "e0", "kind": "corectează"}])
    epi = FakeEpisodic({"e0": ("user: Kent costă 120", T17 - 3 * DAY), "e1": ("user: nu, 140", T17 - DAY)})
    client = FakeClient({"about": ["Render"], "asks_past": False})
    block, shown, texts = _expose(client, epi, "ce facem cu deploy-ul?", [_hit("s1", 0.4, "altceva complet")],
                                  {"nume": "Laris"})
    assert block.startswith("[În minte]")
    assert re.search(r"^- \(2026-11-\d\d\) Prețul pentru Kent", block, re.M)
    assert "e0" in shown and "e1" in shown                       # reached through the graph, not the search
    assert "Prețul pentru Kent a fost 120 de lire." in block
    assert "legat de: render" in block
    assert "Ce știu:\n- nume: Laris" in block
    assert texts["e0"] and "s1" in texts


def test_convergence_puts_search_and_graph_hits_first(on):
    _encode(on, "s1", T17 - DAY, gist="Am ales Render pentru deploy.", about=["Render"])
    _encode(on, "g1", T17 - 2 * DAY, gist="Render a picat azi-noapte.", about=["Render"])
    epi = FakeEpisodic({"g1": ("Render a picat", T17 - 2 * DAY)})
    hits = [_hit("s2", 0.6, "seamănă doar textual"), _hit("s1", 0.5, "am ales Render")]
    block, shown, _ = _expose(FakeClient({"about": ["Render"], "asks_past": False}), epi, "Render?", hits)
    assert shown[0] == "s1"                                      # lit by the search AND the graph: first
    assert set(shown) >= {"s1", "s2", "g1"}
    row = mind.top(on._db, "42", "human", 1, 0.0)[0]
    assert row["item"] == "s1" and set(row["sources"]) == {"căutare", "asociere"}
    assert _amintiri(block)[0].endswith("legat de: render")


def test_open_items_are_shown_until_closed(on):
    _encode(on, "p1", T17, gist="Promit raportul.", opens={"kind": "promisiune", "text": "Trimit raportul Kent"})
    epi = FakeEpisodic({})
    client = FakeClient({"about": [], "asks_past": False})
    block, _, _ = _expose(client, epi, "salut", [])
    assert "Ce e deschis:" in block and "Trimit raportul Kent" in block
    block, _, _ = _expose(client, epi, "altceva", [])
    assert "Trimit raportul Kent" in block                       # still open on the next turn
    _encode(on, "c1", T17 + 60, gist="Am trimis raportul.", closes=["open:p1"])
    block, _, _ = _expose(client, epi, "gata", [])
    assert "Trimit raportul Kent" not in block and "Ce e deschis:" not in block


def test_missing_cue_is_said_and_short_messages_say_nothing(on):
    _encode(on, "r1", T17, gist="Render merge.", about=["Render"])
    epi = FakeEpisodic({"r1": ("Render merge", T17)})
    asks = FakeClient({"about": ["Bucegi", "Render"], "asks_past": True})
    block, _, _ = _expose(asks, epi, "ce am zis despre Bucegi și Render?", [])
    assert "Ce nu știu:\n- nu ai nimic despre Bucegi" in block
    assert "nu ai nimic despre Render" not in block              # in the graph and a lit memory is about it
    short = FakeClient({"about": ["Bucegi"], "asks_past": False})
    block, _, _ = _expose(short, epi, "ok", [])
    assert "Ce nu știu" not in block
    broken = FakeClient("nu e json")
    block, _, _ = _expose(broken, epi, "ce am zis despre Bucegi?", [])
    assert "Ce nu știu" not in block


def test_block_fits_the_budget(on):
    facts = {"nume": "Laris", "oraș": "Iași"}
    hits = [_hit(f"m{i}", 0.9 - i * 0.05, f"amintirea {i} " + "cuvânt lung de umplutură " * 60) for i in range(10)]
    client = FakeClient({"about": [], "asks_past": False})
    full, full_shown, _ = _expose(client, FakeEpisodic({}), "x", hits, facts, budget=100000)
    assert len(full_shown) == 10
    block, shown, texts = _expose(client, FakeEpisodic({}), "x", hits, facts, budget=250, chat="43")
    assert estimate_tokens(block) <= 250
    assert "- nume: Laris" in block and "- oraș: Iași" in block    # Ce știu is never dropped
    assert 0 < len(shown) < 10 and shown == full_shown[:len(shown)]  # the least active go first
    assert set(texts) == set(shown)


def test_understanding_failure_falls_back_to_search_and_carry(on):
    mind.light(on._db, "42", "human", "c1", "amintire", 0.9, "căutare", "seamănă cu ce spui", time.time())
    epi = FakeEpisodic({"c1": ("user: ieri am vorbit de Kent", T17 - DAY)})
    client = FakeClient(exc=RuntimeError("LLM down"))
    block, shown, texts = _expose(client, epi, "și?", [_hit("s1", 0.7, "căutarea a găsit asta")])
    assert set(shown) == {"c1", "s1"}
    assert "ieri am vorbit de Kent" in block and "căutarea a găsit asta" in block
    assert "Ce nu știu" not in block and client.calls                # it was asked, it failed, the turn went on


def test_any_error_returns_empty(on, monkeypatch):
    class Broken:
        def _get_collection(self):
            raise RuntimeError("chroma down")
    mind.light(on._db, "42", "human", "c1", "amintire", 0.9, "căutare", "x", time.time())
    assert _expose(FakeClient({"about": [], "asks_past": False}), Broken(), "x", []) == ("", [], {})
