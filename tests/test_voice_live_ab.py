"""tests.test_voice_live_ab — the A/B script refuses to run without its keys and writes playable WAVs (no network)."""
from __future__ import annotations

import asyncio
import importlib.util
import wave
from pathlib import Path

_spec = importlib.util.spec_from_file_location("voice_live_ab", Path(__file__).parent.parent / "scripts" / "voice_live_ab.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def test_it_is_blocked_until_both_provider_keys_and_deepgram_are_there():
    assert ab.blocked_reason({"GEMINI_API_KEY": "g", "DEEPGRAM_API_KEY": "d"}) == "BLOCKED: missing OPENAI_API_KEY in GOAT's .env"
    assert ab.blocked_reason({"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "DEEPGRAM_API_KEY": "d"}) is None
    assert ab.blocked_reason({"GEMINI_API_KEY": "g", "DEEPGRAM_API_KEY": "d"}, only="gemini") is None
    assert "DEEPGRAM_API_KEY" in ab.blocked_reason({"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"})


def test_main_stops_with_the_reason_before_touching_any_service(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert asyncio.run(ab.main(["--out", str(tmp_path / "x")])) == 2
    assert "BLOCKED" in capsys.readouterr().out and not (tmp_path / "x").exists()


def test_the_lines_mix_small_talk_and_requests_for_goat_and_wavs_are_24_khz_mono(tmp_path):
    assert {answer is None for _, answer in ab.LINES} == {True, False} and len(ab.LINES) >= 4
    ab.write_wav(tmp_path / "a.wav", b"\x01\x00" * 2400, 24000)
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 24000, 2400)
