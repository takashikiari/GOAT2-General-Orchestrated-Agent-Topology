"""orchestrator/mind_hook.assemble through the REAL assemble_blocks (R26, R31): the L1 facts never eat the share L3
is guaranteed, and the turns the conversation block already shows are not repeated in [În minte]."""
from __future__ import annotations

import asyncio
import time

from memory.budget import estimate_tokens
from memory.config import L3_MIN_GUARANTEE_TOKENS
from memory.context_assembler import assemble_blocks
from orchestrator import mind_hook
from tests.test_brain_mind_expose import FakeClient, FakeEpisodic, _encode, _hit, on  # noqa: F401

_FACTS = {
    "nume": "Laris, fondatorul PetVoyageUK, locuiește în Iași și lucrează de acasă aproape în fiecare zi",
    "firma": "PetVoyageUK transportă animale de companie între Marea Britanie și Europa cu șoferi verificați",
    "prețuri": "prețul pentru Kent este calculat pe mile, cu o taxă de odihnă de 150 de lire peste 600 de mile",
    "plăți": "plățile trec prin Stripe; o întrerupere de 5000 de lire a dus la verificări înainte de orice deploy",
    "server": "backend-ul rulează pe Render, frontend-ul pe Vercel, iar baza de date este MongoDB Atlas",
    "LeShuttle": "migrarea la noul API B2B Eurotunnel are termen 31 ianuarie 2027 și este obligatorie",
    "GOAT": "GOAT este asistentul lui Laris pe Telegram, cu memorie pe niveluri L0 până la L3 și un graf",
    "agenți": "Kimi și Claude lucrează împreună prin canalul agent-coord, iar GOAT conduce echipa",
    "limbă": "Laris scrie în română, uneori amestecat cu engleză tehnică, și preferă răspunsuri scurte",
    "program": "Laris lucrează mai ales seara târziu și nu vrea notificări după miezul nopții",
    "comision": "comisionul șoferilor este 28 la sută, cu o variantă în trepte discutată, neimplementată",
    "cotații": "limita de două cotații pe zi pentru anonimi este intenționată, împotriva concurenților",
    "dev": "orice schimbare se testează întâi local pe Dev și abia apoi se face deploy în producție",
    "memorie": "regula absolută: nimic nu se șterge din graful memoriei, legăturile doar se estompează",
    "voce": "vocea GOAT folosește Gemini Live; modul apel telefonic a fost reparat în PR 47",
}


class _RealLayers:
    """The real assembly (memory.context_assembler.assemble_blocks), as MemoryLayers.assemble_context runs it."""

    def __init__(self, episodic):
        self._episodic = episodic

    async def assemble_context(self, chat_id, budget=None, l3_results=None, facts=None, messages=None,
                               identity_prompt=None, temporal_center=None, provenance=None):
        return assemble_blocks(budget, l3_results, facts or {}, identity_prompt, messages or [], 3.0,
                               temporal_center, provenance)


def _messages(n=120):
    now = time.time() - 3600
    out = []
    for i in range(n):
        lid = f"l{i // 2}"
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"mesajul {i} despre drumul spre Dover " + "și ce mai facem " * 40,
                    "timestamp": now + i * 10, "l3_id": lid})
    return out


def _assemble(client, epi, msgs, hits, budget=8000):
    return asyncio.run(mind_hook.assemble(client, _RealLayers(epi), "42", "human", "ce am zis despre Kent?",
                                          budget=budget, l3_results=hits, facts=dict(_FACTS), messages=msgs,
                                          identity_prompt="You are GOAT.", temporal_center=None, provenance={}))


def test_facts_do_not_eat_the_l3_guarantee(on):  # noqa: F811
    _encode(on, "k1", time.time() - 86400, gist="Laris a ales Kent pentru prima rută.", about=["Kent"])
    epi = FakeEpisodic({"k1": ("user: Kent e prima rută", time.time() - 86400)})
    hits = [_hit("k1", 0.9, "user: Kent e prima rută"), _hit("k2", 0.6, "user: Kent costă 140 de lire")]
    client = FakeClient({"about": ["Kent"], "names": ["Kent"], "asks_past": True})
    blocks, used, shown, _, ok = _assemble(client, epi, _messages(), hits)
    assert ok and blocks[-1].startswith("[În minte]")
    history = next(b for b in blocks if b.startswith("[Conversation History]"))
    assert estimate_tokens(history) > 3000                       # L2 is at its full cap
    block = blocks[-1]
    assert "Reguli:" in block and all(f"- {k}: {v}" in block for k, v in _FACTS.items())
    assert "Ce s-a întâmplat:\n- (" in block and used >= 1 and "k1" in shown
    assert "Ce nu știu" not in block and "nu ai nimic" not in block
    ce_stiu = block.split("\n\nCe s-a întâmplat:", 1)[0].split("[În minte]\n\n", 1)[1]
    assert 8000 - sum(estimate_tokens(b) for b in blocks[:-1]) >= estimate_tokens(ce_stiu) + L3_MIN_GUARANTEE_TOKENS


def test_turns_shown_in_the_conversation_are_not_repeated(on):  # noqa: F811
    msgs = _messages(6)
    hits = [_hit("l2", 0.9, msgs[4]["content"]), _hit("old", 0.5, "user: o amintire veche despre Kent")]
    client = FakeClient({"about": [], "names": [], "asks_past": False})
    blocks, _, shown, _, ok = _assemble(client, FakeEpisodic({}), msgs, hits)
    assert ok and shown == ["old"]
