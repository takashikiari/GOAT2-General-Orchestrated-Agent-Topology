import logging

from memory import usage_probe as up

BLOCKS = [
    "You are GOAT",
    "[Conversation History]\nuser: salut",
    up.L3_HEADER + "\n- [ieri] Laris a configurat serverul Letta pe portul 8283 pentru memoria permanentă\n"
    "- [acum 3 zile] rețetă de clătite cu banane și scorțișoară\n  pe două rânduri",
]


def test_split_entries_keeps_multiline_entries():
    entries = up.split_l3_entries(BLOCKS)
    assert len(entries) == 2 and entries[1].endswith("pe două rânduri")
    assert up.split_l3_entries(["fără L3"]) == []


def test_used_vs_unused_is_term_overlap():
    reply = "Serverul Letta rulează pe portul 8283, deci memoria permanentă e disponibilă."
    m = up.measure(BLOCKS, reply)
    assert m["injected"] == 2 and m["used"] == 1
    assert m["entries"][0]["used"] and not m["entries"][1]["used"]
    assert m["unused_tokens"] == m["entries"][1]["tokens"] and m["l3_tokens"] > m["unused_tokens"]


def test_log_line_is_marked_approximate(caplog):
    caplog.set_level(logging.INFO, logger="memory.usage_probe")
    up.log_usage("c1", BLOCKS, "nimic legat")
    assert any("l3_usage chat=c1 approx=term-overlap injected=2 used=0" in r.getMessage() for r in caplog.records)


def test_never_raises_and_skips_without_blocks():
    assert up.log_usage("c1", None, "x") is None
    assert up.log_usage("c1", [None], "x") is None  # malformed input swallowed
