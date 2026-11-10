"""tests.test_voice_live_resume — the live session outlives the provider's connection: on goAway it resumes with the last
handle and the page does not notice; without a handle (or if resuming fails) the page is told, as before."""
from __future__ import annotations

from admin_panel.voice_live import providers
from admin_panel.voice_live.fake import FakeProvider
from voice_live_helpers import FRAME, _live, _types, _until, env  # noqa: F401 - env is a fixture


def _speak(s):
    s.send_bytes(FRAME)
    s.send_bytes(FRAME)


def test_go_away_is_answered_by_resuming_with_the_last_handle_and_the_conversation_goes_on(env):
    script = (("chat", "salut"), ("chat", "încă ceva"))
    env.monkeypatch.setitem(providers.PROVIDERS, "fake", env.make(script=script, go_away_after=1, close_after_go_away=0.3))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        _speak(s)
        first = _until(s, lambda m: m["type"] == "state" and m["state"] == "listening")
        _speak(s)  # after the connection was replaced: still heard and answered
        second = _until(s, lambda m: m["type"] == "user_text" and m["final"])
    provider = env.made[0]
    assert provider.resumed == ["h-1"]  # the handle sent with the first utterance
    assert "notice" not in _types(first + second) and "error" not in _types(first + second)
    assert second[-1]["text"] == "încă ceva"


def test_without_a_handle_the_page_is_told_and_the_provider_closing_ends_it(env):
    env.monkeypatch.setitem(providers.PROVIDERS, "fake",
                            env.make(script=(("chat", "salut"),), go_away_after=1, resumable=False, close_after_go_away=0.05))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        _speak(s)
        notice = _until(s, lambda m: m["type"] == "notice")[-1]
        error = _until(s, lambda m: m["type"] == "error")[-1]
    assert notice == {"type": "notice", "code": "go_away", "seconds": 5.0}
    assert error["code"] == "upstream" and env.made[0].resumed == []


def test_a_resume_that_fails_falls_back_to_telling_the_page(env):
    class Refusing(FakeProvider):
        async def resume(self, handle):
            raise RuntimeError("refused")

    env.monkeypatch.setitem(providers.PROVIDERS, "fake",
                            lambda: Refusing(script=(("chat", "salut"),), go_away_after=1, pace=0.0, tone_seconds=0.1,
                                             trigger_bytes=6400))
    with _live(env) as s:
        _until(s, lambda m: m["type"] == "state")
        _speak(s)
        notice = _until(s, lambda m: m["type"] == "notice")[-1]
    assert notice["code"] == "go_away"
