"""Log hygiene after the 2026-11-17 review of the live log (GOAT + Laris):

1) the Application error handler: a network blip is one WARNING line without traceback, a real error is an ERROR
   with context and traceback; nothing is sent to the chat;
2) cloudflared: a clean exit (rc 0/-15) is INFO only if the bot's shutdown follows it, else a WARNING; any other
   exit is a WARNING with its code;
3) coord_poller: a failing `unread` is retried twice (1 s, 3 s) in the same interval, logged once at the end.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut

from admin_panel import tunnel as tunnel_module
from admin_panel import tunnel_state
from telegram_interface import _coord_poller as p
from telegram_interface import _errors
from tests.test_tunnel_state import _App, _enable, _spawn


# --- 1) error handler ------------------------------------------------------------------------------------------
def _update(chat=1912576407, text="Salut GOAT"):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_message=SimpleNamespace(text=text))


@pytest.mark.parametrize("err", [NetworkError("Bad Gateway"), TimedOut("Timed out")])
def test_a_network_blip_is_one_warning_line_without_traceback(caplog, err):
    caplog.set_level(logging.DEBUG, logger="telegram_interface._errors")
    asyncio.run(_errors.on_error(None, SimpleNamespace(error=err)))
    [rec] = caplog.records
    assert rec.levelno == logging.WARNING and rec.exc_info is None
    assert type(err).__name__ in rec.getMessage() and "update=None" in rec.getMessage()


def test_a_real_handler_error_is_an_error_with_context_and_traceback(caplog):
    caplog.set_level(logging.DEBUG, logger="telegram_interface._errors")
    try:
        raise ValueError("boom in handler")
    except ValueError as exc:
        err = exc
    asyncio.run(_errors.on_error(_update(), SimpleNamespace(error=err)))
    [rec] = caplog.records
    assert rec.levelno == logging.ERROR and rec.exc_info is not None
    assert "chat=1912576407" in rec.getMessage() and "Salut GOAT" in rec.getMessage()


def test_bad_request_is_not_mistaken_for_a_blip(caplog):
    caplog.set_level(logging.DEBUG, logger="telegram_interface._errors")
    asyncio.run(_errors.on_error(_update(), SimpleNamespace(error=BadRequest("can't parse entities"))))
    assert caplog.records[0].levelno == logging.ERROR      # BadRequest IS a NetworkError subclass in PTB


def test_the_app_registers_the_handler():
    from telegram_interface import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert "app.add_error_handler(on_error)" in src


# --- 2) cloudflared exit -----------------------------------------------------------------------------------------
def _eof_after_url(monkeypatch, rc):
    _enable(monkeypatch)
    lines = iter([b"INF |  https://random-example-words.trycloudflare.com  |\n", b""])

    async def readline():
        return next(lines)
    process = _spawn(monkeypatch, readline)

    async def wait():
        process.returncode = rc
        return rc
    process.wait = wait
    return process


@pytest.fixture(autouse=False)
def _fresh_tunnel_state():
    tunnel_state.set_state("off")
    yield
    tunnel_state.set_state("off")


async def _start_then_shutdown(after_s=0.05):
    """What a restart does: cloudflared exits first, the bot's post_shutdown cancels the task ~1 s later."""
    task = asyncio.create_task(tunnel_module.start(_App()))
    await asyncio.sleep(after_s)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("rc", [0, -15])
def test_a_clean_exit_followed_by_the_bot_shutdown_is_info(monkeypatch, caplog, _fresh_tunnel_state, rc):
    caplog.set_level(logging.DEBUG, logger="admin_panel.tunnel")
    _eof_after_url(monkeypatch, rc)
    asyncio.run(_start_then_shutdown())
    recs = [r for r in caplog.records if "cloudflared" in r.getMessage()]
    assert [r.levelno for r in recs] == [logging.INFO] and f"rc={rc}" in recs[0].getMessage()
    assert tunnel_state.get_state()["status"] == "down"


def test_a_clean_exit_while_the_bot_keeps_running_is_a_warning(monkeypatch, caplog, _fresh_tunnel_state):
    caplog.set_level(logging.DEBUG, logger="admin_panel.tunnel")
    monkeypatch.setattr(tunnel_module, "_SHUTDOWN_GRACE_S", 0.05)
    _eof_after_url(monkeypatch, 0)
    asyncio.run(tunnel_module.start(_App()))                                  # nobody cancels it: no shutdown
    recs = [r for r in caplog.records if "cloudflared" in r.getMessage()]
    assert [r.levelno for r in recs] == [logging.WARNING]
    assert "rc=0 while the bot kept running" in recs[0].getMessage()
    assert tunnel_state.get_state()["status"] == "down"


def test_any_other_exit_stays_a_warning_with_its_code(monkeypatch, caplog, _fresh_tunnel_state):
    caplog.set_level(logging.DEBUG, logger="admin_panel.tunnel")
    _eof_after_url(monkeypatch, 1)
    asyncio.run(tunnel_module.start(_App()))
    recs = [r for r in caplog.records if "cloudflared" in r.getMessage()]
    assert [r.levelno for r in recs] == [logging.WARNING] and "rc=1" in recs[0].getMessage()



def test_stderr_closed_but_process_alive_never_hangs_start(monkeypatch, caplog, _fresh_tunnel_state):
    caplog.set_level(logging.DEBUG, logger="admin_panel.tunnel")
    monkeypatch.setattr(tunnel_module, "_EXIT_WAIT_S", 0.05)
    _enable(monkeypatch)
    lines = iter([b"INF |  https://random-example-words.trycloudflare.com  |\n", b""])

    async def readline():
        return next(lines)
    process = _spawn(monkeypatch, readline)
    killed = []
    waits = []

    async def wait():
        waits.append(1)
        if not killed:                       # alive: only a kill ends it
            await asyncio.sleep(3600)
        return process.returncode

    def kill():
        killed.append(1)
        process.returncode = -9
    process.wait, process.kill = wait, kill
    asyncio.run(asyncio.wait_for(tunnel_module.start(_App()), 5))      # returns: no hang
    assert killed == [1]
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("did not exit within" in m for m in msgs)
    assert not any("exited rc=None" in m for m in msgs)                       # no fake 'exited' line after it

# --- 3) coord_poller retry -----------------------------------------------------------------------------------------
_OK = (0, '{"messages": [], "ack": null}', "")
_DOWN = (3, "", "agent-coord: hub inaccesibil: hub read failed: timed out")


def _cli_seq(monkeypatch, *results):
    calls, it = [], iter(results)
    monkeypatch.setattr(p, "_cli", lambda *a: calls.append(a) or next(it))
    sleeps = []
    monkeypatch.setattr(p.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_a_hub_hiccup_is_retried_and_not_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="telegram_interface._coord_poller")
    calls, sleeps = _cli_seq(monkeypatch, _DOWN, _OK)
    assert p._fetch("lenovo/goat/superv") is not None
    assert len(calls) == 2 and sleeps == [1.0]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_hub_that_stays_down_is_logged_once_after_three_attempts(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="telegram_interface._coord_poller")
    calls, sleeps = _cli_seq(monkeypatch, _DOWN, _DOWN, _DOWN)
    assert p._fetch("lenovo/goat/superv") is None
    assert len(calls) == 3 and sleeps == [1.0, 3.0]
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warns) == 1 and "after 3 attempts (exit 3)" in warns[0]
