"""A provider answering 200 with `null` or an error body must not crash the turn (T10, 2026-11-07)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchestrator.llm_response import NO_CHOICES, first_choice
from orchestrator.orchestrator import Orchestrator
from tests._orch_fakes import _FakeAnalytics, _FakeLayers, _FakePluginManager
from tests.test_agentic_loop import _Msg, _Reg, _Resp, _tc, _tool


def test_first_choice_normal():
    c = SimpleNamespace(message="m")
    assert first_choice(SimpleNamespace(choices=[c, "x"])) is c


def test_first_choice_none_and_missing_choices():
    for resp in (None, SimpleNamespace(choices=None), SimpleNamespace(choices=[]), SimpleNamespace()):
        c = first_choice(resp)
        assert c.message.content == "" and c.message.tool_calls is None and c.finish_reason == NO_CHOICES


class _Script:
    """create() returns the scripted items in order; None entries simulate a `null` body."""

    def __init__(self, items):
        self.items, self.calls = list(items), 0

    async def create(self, **_kw):
        self.calls += 1
        item = self.items[min(self.calls - 1, len(self.items) - 1)]
        return None if item is None else _Resp(item)


def _run(items, tools=()):
    script = _Script(items)
    llm = SimpleNamespace(chat=SimpleNamespace(completions=script))
    layers = _FakeLayers(results=[])
    reg = _Reg(layers, llm, _FakeAnalytics())
    orch = Orchestrator(layers=reg.memory_layers, llm_client=llm, plugin_manager=_FakePluginManager(),
                        analytics=reg.memory_analytics, tools=list(tools))
    return asyncio.run(orch.run("q", "chat")), script


def test_null_body_in_tool_loop_does_not_crash():
    # exactly the 09:51:58 shape: first call asks for a tool, the loop call comes back None
    reply, script = _run([_Msg(tool_calls=[_tc("a1", "t")]), None, _Msg(content="recovered")],
                         tools=[_tool("t", "result")])
    assert isinstance(reply, str) and script.calls >= 2


def test_null_body_on_first_call_does_not_crash():
    reply, _ = _run([None, _Msg(content="ok")])
    assert isinstance(reply, str)
