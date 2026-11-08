"""tests.test_chat_service — the admin panel chat: one GOAT conversation shared with Telegram, live turn events."""
from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from orchestrator import turn_events
from orchestrator.chat_service import ChatBusy, ChatService, ChatUnavailable


class _Run:
    """Stand-in for Orchestrator.run: emits one tool call, optionally waits on a gate, echoes the text."""

    def __init__(self, gate: bool = False, fail: bool = False, reply: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.gate = asyncio.Event() if gate else None
        self.fail, self.reply, self.cancelled = fail, reply, False

    async def __call__(self, text, chat_id, *, on_context_assembled=None):
        self.calls.append((text, chat_id))
        if on_context_assembled:
            on_context_assembled("blocks")
        turn_events.emit({"type": "tool_start", "id": "t1", "name": "shell_run", "args": '{"cmd": "ls"}'})
        try:
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise RuntimeError("boom with internal details")
        turn_events.emit({"type": "tool_end", "id": "t1", "name": "shell_run", "result": "file.txt"})
        return self.reply if self.reply is not None else f"re: {text}"


def _svc(run, locks=None, chat_id="42", **kw):
    return ChatService(run=run, locks=locks if locks is not None else defaultdict(asyncio.Lock),
                       chat_id=lambda: chat_id, **kw)


async def _until(pred, tries=200):
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def _types(view):
    return [e["type"] for e in view["events"]]


def test_a_turn_runs_and_reports_live_events_in_order():
    async def main():
        run = _Run()
        svc = _svc(run)
        turn = svc.submit("salut")
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        view = svc.get(turn.id)
        assert _types(view) == ["started", "tool_start", "tool_end", "reply"]
        assert [e["seq"] for e in view["events"]] == [1, 2, 3, 4]
        assert view["reply"] == "re: salut" and run.calls == [("salut", "42")]
        assert view["events"][1]["name"] == "shell_run" and view["events"][2]["ok"] is True
    asyncio.run(main())


def test_get_after_returns_only_newer_events_and_unknown_turn_is_none():
    async def main():
        svc = _svc(_Run())
        turn = svc.submit("x")
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        assert [e["seq"] for e in svc.get(turn.id, after=2)["events"]] == [3, 4]
        assert svc.get("nope") is None
    asyncio.run(main())


def test_it_waits_for_the_same_lock_the_telegram_bot_uses():
    async def main():
        locks = defaultdict(asyncio.Lock)
        run = _Run()
        svc = _svc(run, locks)
        await locks["42"].acquire()                      # a Telegram turn is running
        turn = svc.submit("din panou")
        await _until(lambda: "queued" in _types(svc.get(turn.id)))
        assert svc.get(turn.id)["status"] == "queued" and run.calls == []
        assert svc.state()["busy"] is True and svc.state()["external_busy"] is True
        locks["42"].release()                            # Telegram turn finished
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        assert run.calls == [("din panou", "42")]
    asyncio.run(main())


def test_two_panel_turns_never_overlap():
    async def main():
        run = _Run(gate=True)
        svc = _svc(run)
        a, b = svc.submit("a"), svc.submit("b")
        await _until(lambda: svc.get(a.id)["status"] == "running")
        assert svc.get(b.id)["status"] == "queued" and [c[0] for c in run.calls] == ["a"]
        run.gate.set()
        await _until(lambda: svc.get(b.id)["status"] == "done")
        assert [c[0] for c in run.calls] == ["a", "b"]
    asyncio.run(main())


def test_queue_limit_and_input_validation():
    async def main():
        svc = _svc(_Run(gate=True), max_queue=2, max_text=10)
        svc.submit("1"); svc.submit("2")
        with pytest.raises(ChatBusy):
            svc.submit("3")
        with pytest.raises(ValueError):
            _svc(_Run()).submit("   ")
        with pytest.raises(ValueError):
            svc.submit("x" * 11)
    asyncio.run(main())


def test_no_admin_chat_means_unavailable():
    async def main():
        with pytest.raises(ChatUnavailable):
            _svc(_Run(), chat_id=None).submit("hi")
    asyncio.run(main())


def test_cancel_a_running_turn_releases_the_lock():
    async def main():
        locks = defaultdict(asyncio.Lock)
        run = _Run(gate=True)
        svc = _svc(run, locks)
        turn = svc.submit("lung")
        await _until(lambda: svc.get(turn.id)["status"] == "running")
        assert svc.cancel(turn.id) is True
        await _until(lambda: svc.get(turn.id)["status"] == "cancelled")
        assert run.cancelled and "cancelled" in _types(svc.get(turn.id)) and not locks["42"].locked()
        assert svc.cancel(turn.id) is False              # already finished
    asyncio.run(main())


def test_cancel_a_queued_turn_means_it_never_runs():
    async def main():
        locks = defaultdict(asyncio.Lock)
        run = _Run()
        svc = _svc(run, locks)
        await locks["42"].acquire()
        turn = svc.submit("nu")
        await _until(lambda: "queued" in _types(svc.get(turn.id)))
        assert svc.cancel(turn.id) is True
        await _until(lambda: svc.get(turn.id)["status"] == "cancelled")
        locks["42"].release()
        await asyncio.sleep(0.05)
        assert run.calls == []
    asyncio.run(main())


def test_an_error_gives_a_generic_message_and_frees_the_lock():
    async def main():
        locks = defaultdict(asyncio.Lock)
        svc = _svc(_Run(fail=True), locks)
        turn = svc.submit("x")
        await _until(lambda: svc.get(turn.id)["status"] == "error")
        view = svc.get(turn.id)
        assert "boom" not in str(view) and view["events"][-1]["type"] == "error" and not locks["42"].locked()
    asyncio.run(main())


def test_an_empty_reply_is_replaced_by_a_visible_message():
    async def main():
        svc = _svc(_Run(reply="   "))
        turn = svc.submit("x")
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        assert svc.get(turn.id)["reply"].strip()
    asyncio.run(main())


def test_event_payloads_are_truncated():
    async def main():
        class Big(_Run):
            async def __call__(self, text, chat_id, *, on_context_assembled=None):
                turn_events.emit({"type": "tool_start", "id": "t", "name": "n", "args": "a" * 5000})
                turn_events.emit({"type": "tool_end", "id": "t", "name": "n", "result": '{"error": "' + "r" * 5000})
                return "ok"
        svc = _svc(Big())
        turn = svc.submit("x")
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        ev = svc.get(turn.id)["events"]
        assert len(ev[1]["args"]) <= 301 and len(ev[2]["preview"]) <= 301
        assert ev[2]["chars"] > 5000 and ev[2]["ok"] is False
    asyncio.run(main())


def test_usage_is_logged_and_a_logging_failure_is_harmless():
    async def main():
        seen = []
        svc = _svc(_Run(), log_usage=lambda chat, injected, reply: seen.append((chat, injected, reply)))
        turn = svc.submit("x")
        await _until(lambda: svc.get(turn.id)["status"] == "done")
        assert seen == [("42", "blocks", "re: x")]

        def bad(*_a):
            raise RuntimeError("probe down")
        svc2 = _svc(_Run(), log_usage=bad)
        t2 = svc2.submit("y")
        await _until(lambda: svc2.get(t2.id)["status"] == "done")
    asyncio.run(main())


def test_old_turns_are_dropped_beyond_max_turns():
    async def main():
        svc = _svc(_Run(), max_turns=3)
        ids = []
        for i in range(5):
            t = svc.submit(str(i))
            ids.append(t.id)
            await _until(lambda: svc.get(t.id)["status"] == "done")
        assert svc.get(ids[0]) is None and svc.get(ids[-1]) is not None
    asyncio.run(main())
