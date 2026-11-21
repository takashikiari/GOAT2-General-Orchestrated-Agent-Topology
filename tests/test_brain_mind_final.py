"""brain/mind_live.py after the final review (rulings R24, R27, R30, R31, R32, R36, batch commits): "nu știu" only
for named things the graph does not know, open items age, no repeat of the conversation, clean raw lines, the turn's
own time."""
from __future__ import annotations

import asyncio
import time

from brain import brain as b
from brain import mind
from brain import mind_live as ml
from tests.test_brain_mind_expose import T17, FakeClient, FakeEpisodic, _encode, _expose, _hit, on  # noqa: F401

HOUR = 3600.0


def test_missing_is_decided_by_the_graph_and_only_for_names(on):  # noqa: F811
    _encode(on, "r1", T17, gist="Render merge.", about=["Render"])
    on._db.execute("INSERT INTO graph_nodes (id, kind, label, created_at) VALUES ('thing:bucegi', 'thing', "
                   "'bucegi', 1.0)")                              # a node, but no episode is about it
    on._db.commit()
    payload = {"about": ["Render", "omisiune", "Bucegi", "Ceahlău"], "names": ["Render", "Bucegi"],
               "asks_past": True}
    block, shown, _ = _expose(FakeClient(payload), FakeEpisodic({}), "ce am zis despre Render și Bucegi?", [])
    assert "- nu ai nimic despre Bucegi" in block
    assert "Render" not in block.split("Ce nu știu:", 1)[1]
    assert "omisiune" not in block and "Ceahlău" not in block    # not names: never reported missing


def test_old_open_items_are_not_lit_and_memories_stay_in_mind(on):  # noqa: F811
    now = time.time()
    for i in range(20):
        ts = now - (48 * HOUR + i) if i < 15 else now - (i - 14) * 60
        _encode(on, f"p{i}", ts, gist=f"Promit {i}.", opens={"kind": "promisiune", "text": f"Deschis numărul {i}"})
    hits = [_hit(f"m{i}", 0.9 - i * 0.05, f"amintirea {i} despre Kent") for i in range(8)]
    block, shown, texts = _expose(FakeClient({"about": [], "names": [], "asks_past": False}), FakeEpisodic({}),
                                  "x", hits)
    lit = {r["item"] for r in mind.top(on._db, "42", "human", 100, 0.0) if r["kind"] == "deschis"}
    assert lit and lit <= {f"open:p{i}" for i in range(15, 20)} and len(lit) <= b.MIND["expose_items"] // 3
    assert not any(f"Deschis numărul {i} —" in block for i in range(15)) and "Deschis numărul 15" in block
    turn = FakeClient({"type": "eveniment"})
    asyncio.run(ml.after_turn(turn, "42", "human", "t1", "x", "ok", shown, texts))
    prompt = turn.calls[0]["messages"][-1]["content"]
    assert sum(f"m{i} — amintire" in prompt for i in range(8)) >= 4   # not crowded out by open items
    assert prompt.count("— deschis —") <= 4


def test_turns_already_in_the_conversation_are_not_shown(on):  # noqa: F811
    hits = [_hit("l2", 0.9, "ce s-a spus acum un minut"), _hit("s1", 0.5, "o amintire veche despre Kent")]
    block, shown, _ = asyncio.run(ml.expose(FakeClient({"about": [], "names": [], "asks_past": False}),
                                            FakeEpisodic({}), "42", "human", "x", hits, {}, 4000,
                                            exclude_ids={"l2"}))
    assert shown == ["s1"] and "acum un minut" not in block


def test_raw_lines_lose_timestamp_and_role_and_never_repeat_the_gist(on):  # noqa: F811
    _encode(on, "g1", T17, gist="Kent costă 140 de lire.")
    epi = FakeEpisodic({"g1": ("Kent costă 140 de lire.", T17),
                        "long": ("[2026-11-14T17:38:23+02:00] user: " + "drum lung spre Dover " * 20, T17)})
    hits = [_hit("raw", 0.9, "[2026-11-14T17:38:23+02:00] user: Kent costă 120\nassistant: notat")]
    mind.light(on._db, "42", "human", "g1", "amintire", 0.8, "căutare", "x", time.time())
    mind.light(on._db, "42", "human", "long", "amintire", 0.7, "căutare", "x", time.time())
    block, shown, _ = _expose(FakeClient({"about": [], "names": [], "asks_past": False}), epi, "Kent?", hits)
    assert ") Kent costă 120 assistant: notat — " in block and "] user:" not in block.split("„")[0]
    assert "„Kent costă 140" not in block                          # the excerpt only repeated the gist
    assert ") drum lung spre Dover" in block and "„…" in block      # the rest of a long memory follows once
    assert set(shown) == {"raw", "g1", "long"}


def test_the_turn_is_encoded_at_its_own_time(on):  # noqa: F811
    client = FakeClient({"type": "fapt", "gist": "g", "about": ["Kent"]})
    asyncio.run(ml.after_turn(client, "42", "human", "t1", "ieri", "ok", [], {}, ts=T17))
    assert on._db.execute("SELECT ts FROM graph_nodes WHERE id = 't1'").fetchone()[0] == T17
    assert on._db.execute("SELECT created_at FROM graph_edges WHERE src = 't1' AND kind = 'despre'"
                          ).fetchone()[0] == T17


def test_light_can_defer_its_commit(on):  # noqa: F811
    mind.light(on._db, "42", "human", "a", "amintire", 0.5, "căutare", "x", time.time(), commit=False)
    assert on._db.in_transaction
    on._db.commit()
    mind.light(on._db, "42", "human", "a", "amintire", 0.1, "căutare", "x", time.time())
    assert not on._db.in_transaction


def test_the_turn_understanding_sees_the_things_in_mind_to_reuse(on):  # noqa: F811
    _encode(on, "a", T17, gist="Am ales Render.", about=["Render", "PetVoyageUK"])
    mind.light(on._db, "42", "human", "a", "amintire", 0.9, "căutare", "x", time.time())
    turn = FakeClient({"type": "eveniment"})
    asyncio.run(ml.after_turn(turn, "42", "human", "t1", "x", "ok", [], {}))
    prompt = turn.calls[0]["messages"][-1]["content"]
    known = prompt.split("Lucruri cunoscute (etichete existente):\n", 1)[1].split("\n\n", 1)[0]
    assert set(known.splitlines()) == {"- render", "- petvoyageuk"}

