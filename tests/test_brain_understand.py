"""brain/understand.py: the LLM step that classifies a finished turn and extracts cues from a message."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from brain import understand as u
from config import settings

IN_MIND = [
    {"item": "m1", "kind": "amintire", "activation": 0.8, "why": "x", "text": "Am vorbit despre Rust"},
    {"item": "o1", "kind": "deschis", "activation": 0.7, "why": "promisiune", "gist": "Trimit raportul"},
]


class FakeClient:
    def __init__(self, content, finish="stop", exc=None):
        self.calls = []
        self._content, self._finish, self._exc = content, finish, exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self._finish)])


def _run(coro):
    return asyncio.run(coro)


def test_turn_is_classified_and_links_only_to_shown_ids():
    payload = {"type": "decizie", "importance": 1.7, "gist": "Decidem Rust.", "about": ["Rust", "", 3],
               "links": [{"to": "m1", "kind": "continuă"}, {"to": "zzz", "kind": "cauză"},
                         {"to": "m1", "kind": "vecin"}],
               "opens": {"kind": "promisiune", "text": "Trimit mâine"}, "closes": ["o1", "m1", "nope"]}
    r = _run(u.understand_turn(FakeClient(json.dumps(payload)), "salut", "ok", IN_MIND))
    assert r["type"] == "decizie" and r["importance"] == 1.0
    assert r["about"] == ["Rust"]
    assert r["links"] == [{"to": "m1", "kind": "continuă"}]
    assert r["opens"] == {"kind": "promisiune", "text": "Trimit mâine"}
    assert r["closes"] == ["o1"]


def test_unknown_type_and_bad_opens_are_normalised():
    payload = {"type": "altceva", "importance": "mare", "gist": "g", "opens": {"kind": "x", "text": "t"}}
    r = _run(u.understand_turn(FakeClient(json.dumps(payload)), "a", "b", []))
    assert r["type"] == "eveniment" and r["importance"] == 0.5 and r["opens"] is None
    assert r["links"] == [] and r["closes"] == [] and r["about"] == []


def test_json_inside_code_fence_is_parsed():
    c = 'Iată:\n```json\n{"about": ["Rust"], "asks_past": true}\n```'
    assert _run(u.understand_message(FakeClient(c), "ce am zis?", [])) == {"about": ["Rust"], "names": [], "asks_past": True}


def test_invalid_or_empty_reply_returns_none():
    assert _run(u.understand_turn(FakeClient("nu e json"), "a", "b", [])) is None
    assert _run(u.understand_turn(FakeClient(""), "a", "b", [])) is None
    assert _run(u.understand_turn(FakeClient("", finish="length"), "a", "b", [])) is None
    assert _run(u.understand_message(FakeClient("{broken"), "a", [])) is None
    assert _run(u.understand_turn(FakeClient("", exc=RuntimeError("boom")), "a", "b", [])) is None


def test_message_cues():
    r = _run(u.understand_message(FakeClient('{"about": ["Rust", " "], "asks_past": "da"}'), "x", IN_MIND))
    assert r == {"about": ["Rust"], "names": [], "asks_past": False}


def test_request_uses_model_name_and_max_tokens_4000():
    c = FakeClient('{"about": [], "asks_past": false}')
    _run(u.understand_message(c, "salut", IN_MIND))
    kw = c.calls[0]
    assert kw["model"] == settings.MODEL_NAME and kw["max_tokens"] == 4000
    text = json.dumps(kw["messages"], ensure_ascii=False)
    assert "o1 — deschis — Trimit raportul" in text and "salut" in text


def test_names_are_short_concrete_strings():
    c = '{"about": ["Kent"], "names": ["Kent", "", 3, "Render", "o frază foarte lungă despre ceva"], "asks_past": 1}'
    r = _run(u.understand_message(FakeClient(c), "x", []))
    assert r == {"about": ["Kent"], "names": ["Kent", "Render"], "asks_past": False}
    assert '"names"' in u.MESSAGE_PROMPT


def test_turn_uses_8000_tokens_known_labels_and_single_pass_placeholders():
    c = FakeClient('{"type": "fapt"}')
    _run(u.understand_turn(c, "scriu {reply} și {in_mind}", "GOAT {user}", IN_MIND, ["render", "kent"]))
    kw = c.calls[0]
    assert kw["max_tokens"] == 8000
    prompt = kw["messages"][-1]["content"]
    assert "Utilizator: scriu {reply} și {in_mind}\nGOAT: GOAT {user}" in prompt   # user text is never substituted
    assert "- render\n- kent" in prompt and "1-3 cuvinte" in prompt
    m = FakeClient('{"about": []}')
    _run(u.understand_message(m, "despre {in_mind}", IN_MIND))
    assert "Mesaj: despre {in_mind}" in m.calls[0]["messages"][-1]["content"]
