"""Polish wave 2 (Q2-Q6): misclassified rules recoverable, cue names light knowledge, name matching, open items once,
no tool-call junk as memories."""
from __future__ import annotations

from brain import mind_facts as mf
from tests.test_brain_mind_expose import FakeClient, FakeEpisodic, _expose, on  # noqa: F401 - `on` is a fixture


def test_names_match_whole_words_with_diacritics_folded():
    facts = {"brother_name": "Fratele lui Laris se numește Ionuț.", "report": "O analiză de cost."}
    assert mf.mentioned(facts, "Ionut") and mf.mentioned(facts, "IONUȚ") and mf.mentioned(facts, "ionuţ")
    assert not mf.mentioned(facts, "Ana")                       # "analiză" is not Ana
    assert not mf.mentioned(facts, "Ion")                       # nor is "Ionuț" Ion
    assert mf.mentioned(facts, "brother")                       # a key's words count too
    assert not mf.mentioned(facts, "  ")


FACTS = {"emoji_style": "Laris vrea emoji în fiecare mesaj.",
         "brother_name": "Fratele lui Laris se numește Ionuț.",
         "machine_lenovo": "Laptopul lui Laris: Lenovo L470."}


def _section(block, title):
    return block.split(f"{title}:\n", 1)[1].split("\n\n", 1)[0] if f"{title}:\n" in block else ""


def test_a_fact_a_cue_name_points_to_is_shown(on):  # noqa: F811
    """Q3: "not missing" means shown: the name lights the fact even when the understanding did not list it."""
    mf.save(on._db, {"emoji_style": "regulă", "brother_name": "cunoștință", "machine_lenovo": "cunoștință"}, 1.0)
    asks = FakeClient({"about": [], "names": ["Ionut"], "asks_past": True, "facts": []})
    block, _, _ = _expose(asks, FakeEpisodic({}), "ce mai face Ionut?", [], FACTS)
    assert "brother_name: Fratele lui Laris" in _section(block, "Ce știu")
    assert "machine_lenovo" not in block and "nu ai nimic despre Ionut" not in block
    failed = FakeClient("nu e json")                           # understanding failed: no cues, nothing extra
    block, _, _ = _expose(failed, FakeEpisodic({}), "ce mai face Ionut?", [], FACTS, chat="43")
    assert "Ce știu" not in block and "emoji_style" in _section(block, "Reguli")


def test_an_older_fact_kind_table_gets_the_value_hash_column(tmp_path):
    import sqlite3

    from brain import mind
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE fact_kind (key TEXT PRIMARY KEY, kind TEXT NOT NULL, classified_at REAL)")
    db.execute("INSERT INTO fact_kind VALUES ('user_name', 'cunoștință', 1.0)")
    db.commit()
    db.executescript(mind.SCHEMA)
    mind.migrate(db)
    mind.migrate(db)                                            # idempotent
    assert "value_hash" in {r[1] for r in db.execute("PRAGMA table_info(fact_kind)")}
    assert db.execute("SELECT key, kind, value_hash FROM fact_kind").fetchall() == [("user_name", "cunoștință", None)]


def test_a_fact_is_classified_again_when_its_value_changes(on):  # noqa: F811
    """Q2: a misclassified rule is recoverable: a changed value (or a row from before the hash) is asked again."""
    import asyncio
    on._db.execute("INSERT INTO fact_kind (key, kind, classified_at) VALUES ('user_name', 'cunoștință', 1.0)")
    on._db.commit()                                             # classified before value_hash existed
    facts = {"user_name": "Îl cheamă Laris; spune-i Laris.", "brother_name": "Fratele lui Laris: Ionuț."}
    client = FakeClient({"user_name": "regulă", "brother_name": "cunoștință"})
    assert asyncio.run(mf.classify(on, client, facts)) == 2
    assert mf.kinds(on._db) == {"user_name": "regulă", "brother_name": "cunoștință"}
    assert asyncio.run(mf.classify(on, client, facts)) == 0 and len(client.calls) == 1   # same values: no call
    again = FakeClient({"brother_name": "regulă"})
    assert asyncio.run(mf.classify(on, again, {**facts, "brother_name": "Spune-i lui Ionuț „frate”."})) == 1
    prompt = again.calls[0]["messages"][-1]["content"]
    assert "brother_name: Spune-i" in prompt and "user_name:" not in prompt.split("Fapte:", 1)[1]
    assert mf.kinds(on._db)["brother_name"] == "regulă"
    assert on._db.execute("SELECT COUNT(*) FROM fact_kind").fetchone()[0] == 2           # updated, never removed


def test_the_prompt_makes_every_turn_facts_rules():
    p = mf.CLASSIFY_PROMPT
    assert "nume" in p and "limb" in p and "rol" in p and "permisiuni" in p and "stil" in p
    assert "Dacă nu ești sigur" in p and "„regulă”" in p.split("Dacă nu ești sigur", 1)[1][:80]


def test_a_key_the_model_keeps_omitting_is_asked_at_most_three_times(on):  # noqa: F811
    import asyncio
    client = FakeClient({})                                     # never answers for "x"
    for _ in range(5):
        assert asyncio.run(mf.classify(on, client, {"x": "y"})) == 0
    assert len(client.calls) == 3
    assert asyncio.run(mf.classify(on, FakeClient({"x": "regulă"}), {"x": "altă valoare"})) == 1   # a new value
