"""tests.test_voice_live_transcript — what Laris says in the live voice is not lost (Laris, 2026-11-10): his words go to
GOAT verbatim with the request they belong to, what never reached GOAT (small talk) goes to GOAT's memory as one turn
when the conversation ends, and every final transcript is a log line (to compare transcript and memory)."""
from __future__ import annotations

import logging
import time

from admin_panel.voice_live import providers
from admin_panel.voice_live.transcript import Heard
from voice_live_helpers import FRAME, _live, _until, env  # noqa: F401 - env is a fixture


def _speak(s):
    s.send_bytes(FRAME)
    s.send_bytes(FRAME)


def _wait_asked(env, n, seconds=3.0):
    end = time.monotonic() + seconds
    while len(env.asked) < n and time.monotonic() < end:
        time.sleep(0.02)
    return env.asked


def test_the_request_carries_what_was_said_and_small_talk_is_one_memory_turn_at_the_end(env):
    script = (("chat", "bună dimineața"), ("tool", "verifică logurile din ultima oră"), ("chat", "mulțumesc mult"))
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=script))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        _speak(s)
        _until(s, lambda m: m["type"] == "state" and m["state"] == "listening")
        _speak(s)
        _until(s, lambda m: m["type"] == "tool" and m["status"] == "done")
        _speak(s)
        _until(s, lambda m: m["type"] == "user_text" and m["final"] and m["text"] == "mulțumesc mult")
        s.send_json({"type": "close"})
        _until(s, lambda m: m["type"] == "closed")
    asked = _wait_asked(env, 2)
    assert asked[0] == ("verifică logurile din ultima oră\n\n"
                        "(Laris a spus, transcris: «bună dimineața» «verifică logurile din ultima oră»)")
    assert len(asked) == 2 and asked[1].startswith("(Din conversația vocală Live, de reținut.)")
    assert "«mulțumesc mult»" in asked[1] and "verifică logurile" not in asked[1]  # nothing twice, nothing lost


def test_a_conversation_where_nothing_was_said_leaves_nothing_behind(env):
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_json({"type": "close"})
        _until(s, lambda m: m["type"] == "closed")
    time.sleep(0.1)
    assert env.asked == []


def test_every_final_transcript_is_a_log_line_and_partial_ones_are_not(caplog):
    heard = Heard()
    with caplog.at_level(logging.INFO, logger="admin_panel.voice_live.transcript"):
        heard.add("salut")
        heard.add("   ")
    lines = [r.getMessage() for r in caplog.records if r.name == "admin_panel.voice_live.transcript"]
    assert lines == ["voice_live: Laris said (transcript): salut"]


def test_the_words_are_kept_within_what_the_chat_accepts():
    heard = Heard(max_chars=60)
    for i in range(20):
        heard.add(f"propoziția numărul {i}")
    note = heard.end_note()
    assert len(note) <= 60 + len(Heard.NOTE_HEAD) + 40 and "numărul 19" in note  # the newest words are kept
    assert heard.end_note() is None  # taken once
    assert heard.with_words("cerere", heard.take()) == "cerere"  # nothing left to add


def test_words_of_a_request_that_did_not_reach_goat_are_put_back_in_order():
    heard = Heard()
    heard.add("unu")
    words = heard.take()
    heard.add("doi")
    heard.restore(words)  # GOAT was busy
    assert heard.end_note().endswith("«unu» «doi»")
