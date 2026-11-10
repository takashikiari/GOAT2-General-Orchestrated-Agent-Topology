"""tests.test_voice_live — /api/voice/live: a conversation from the page's side (the scripted fake provider, a real
ChatService with a fake GOAT)."""
from __future__ import annotations

from admin_panel.voice_live import providers
from admin_panel.voice_live.fake import FakeProvider
from admin_panel.voice_live.provider import ProviderError, ToolCall, ToolCancel
from voice_live_helpers import FRAME, WorkingMemory, _live, _read, _types, _until, env  # noqa: F401


def test_a_spoken_request_goes_to_goat_and_the_answer_is_spoken_back(env):
    with _live(env) as s:
        assert _read(s) == {"type": "ready", "in_rate": 16000, "out_rate": 24000}
        assert _read(s) == {"type": "state", "state": "listening"}
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)  # 6400 bytes: the fake "hears" the first utterance
        seen = _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        kinds = _types(seen)
        assert {"type": "user_text", "text": "verifică logurile", "final": False} in seen
        assert {"type": "user_text", "text": "verifică logurile din ultima oră", "final": True} in seen
        assert {"type": "assistant_text", "text": "O secundă, verific."} in seen  # the filler, while GOAT works
        assert "audio" in kinds and {"type": "tool", "name": "get_recent_logs", "status": "running"} in seen
        # the answer comes back and is spoken after the model is idle
        rest = _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
        assert rest[-1]["text"] == "Am aflat: Totul e în regulă, 3 erori."
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    provider = env.made[0]
    assert env.asked == ["verifică logurile din ultima oră\n\n(Laris a spus, transcris: «verifică logurile din ultima oră»)"]
    assert provider.tool_results == [("call-1", "ask_goat", "Totul e în regulă, 3 erori.", "WHEN_IDLE")]


def test_small_talk_is_answered_by_the_voice_model_alone(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "mulțumesc"),)))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        spoken = _until(s, lambda m: m["type"] == "assistant_text")[-1]
        assert spoken["text"] == "Cu plăcere. Ai spus: mulțumesc"
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    assert env.made[0].tool_results == []  # GOAT was not asked during the conversation
    # what was said still reaches GOAT's memory: one turn when the conversation ends (tests/test_voice_live_transcript.py)
    assert env.asked == [] or env.asked[0].startswith("(Din conversația vocală Live")


def test_a_tap_cuts_the_model_off(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "salut"),)))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m["type"] == "audio")
        s.send_json({"type": "interrupt"})
        _until(s, lambda m: m["type"] == "interrupted")
    assert env.made[0].interrupts == 1


def test_a_cancelled_tool_call_is_answered_silently_and_goat_still_finishes(env):
    class Cancelling(FakeProvider):
        async def _hear(self, kind, text):
            self._queue.put_nowait(ToolCall(id="c9", name="ask_goat", args={"request": text}))
            self._queue.put_nowait(ToolCancel(("c9",)))

    env.monkeypatch.setitem(providers.PROVIDERS, "fake", lambda: env.made.append(Cancelling((("tool", "verifică"),), pace=0.0, trigger_bytes=6400)) or env.made[-1])
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        _until(s, lambda m: m == {"type": "state", "state": "listening"})
    assert env.made[0].tool_results == [("c9", "ask_goat", "Totul e în regulă, 3 erori.", "SILENT")]


def test_a_second_request_while_goat_is_busy_is_told_to_wait(env):
    env.run.delay = 0.5
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("tool", "prima"), ("tool", "a doua"))))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        for _ in range(4):
            s.send_bytes(FRAME)
        _until(s, lambda m: m == {"type": "tool", "name": "get_recent_logs", "status": "done"})
        _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
    results = {r[0]: r[2] for r in env.made[0].tool_results}
    assert results["call-2"].startswith("GOAT is still working")
    assert results["call-1"] == "Totul e în regulă, 3 erori."
    assert env.asked[0] == "prima\n\n(Laris a spus, transcris: «prima»)"  # the busy one did not reach GOAT
    # ... so its words were kept and reach GOAT's memory at the end of the conversation, not lost
    assert len(env.asked) == 1 or ("«a doua»" in env.asked[1] and "«prima»" not in env.asked[1])


def test_goat_failing_or_an_unknown_tool_is_reported_to_the_model_in_words(env):
    env.run.fail = True
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Am aflat"))
    assert env.made[0].tool_results[0][2] == "GOAT could not finish that request."


def test_the_session_starts_from_the_recent_chat_without_agent_digests_or_tool_evidence(env):
    env.registry.working_memory = WorkingMemory([
        {"role": "user", "content": "ce ai făcut azi?", "timestamp": 1},
        {"role": "assistant", "content": "[Tool calls]\ncalled get_recent_logs({})\n\nAm verificat logurile.", "timestamp": 2},
        {"role": "user", "content": "x" * 800, "timestamp": 3},
    ])
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
    items = env.made[0].history
    assert [(i.role, i.text) for i in items[:2]] == [("user", "ce ai făcut azi?"), ("model", "Am verificat logurile.")]
    assert items[2].role == "user" and len(items[2].text) == 601 and items[2].text.endswith("…")  # cut at 600 characters
    assert len(items) == 3
    assert env.made[0].config.tools[0]["name"] == "ask_goat" and "Romanian" in env.made[0].config.system_prompt


def test_the_conversation_still_starts_when_the_history_cannot_be_read(env):
    env.registry.working_memory = WorkingMemory(fail=True)
    with _live(env) as s:
        assert _read(s)["type"] == "ready"
    assert env.made[0].history == []


def test_the_provider_going_away_is_passed_on_and_an_error_ends_the_session(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=(("chat", "salut"),), go_away_after=1, resumable=False))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)
        notice = _until(s, lambda m: m["type"] == "notice")[-1]
        assert notice == {"type": "notice", "code": "go_away", "seconds": 5.0}

    class Failing(FakeProvider):
        async def send_audio(self, pcm):
            self._queue.put_nowait(ProviderError("quota", "quota exceeded"))

    env.monkeypatch.setitem(providers.PROVIDERS, "fake", lambda: Failing())
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        err = _until(s, lambda m: m["type"] == "error")[-1]
        assert err["code"] == "upstream"


def test_a_tap_is_answered_before_anything_that_comes_after_it(env):
    """``interrupted`` must reach the page before the next utterance's events, or the page would cut off newer speech."""
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)  # the first utterance: the model says its filler
        _until(s, lambda m: m["type"] == "audio")
        s.send_json({"type": "interrupt"})
        s.send_bytes(FRAME)
        s.send_bytes(FRAME)  # the second utterance, right behind the tap
        seen = _until(s, lambda m: m["type"] == "assistant_text" and m["text"].startswith("Cu plăcere"))
    kinds = _types(seen)
    assert "interrupted" in kinds
    assert kinds.index("interrupted") < kinds.index("user_text")


def test_the_pages_microphone_signal_reaches_the_provider(env):
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        s.send_json({"type": "mic", "on": True})
        s.send_json({"type": "mic", "on": False})
        s.send_json({"type": "interrupt"})  # a round trip: once this is seen, the mic messages before it were handled
        _until(s, lambda m: m["type"] == "interrupted")
    assert env.made[0].mic_events == [True, False]
