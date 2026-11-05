import json
import os
import subprocess

from tools import agent_coord_cli as acli
from tools import agent_coord_skill_config as cfg
from tools.goat_skills import agent_coord_status as skill


def tools():
    return {t.name: t for t in skill.build(object())}


def fake(monkeypatch, result, calls=None):
    async def run(*args):
        if calls is not None:
            calls.append(args)
        return result
    monkeypatch.setattr(acli, "run", run)


def test_build_exposes_three_tools():
    assert set(tools()) == {"agent_coord_sessions", "agent_coord_journal", "agent_coord_send", "agent_coord_inbox"}


async def test_sessions_tool_labels_information(monkeypatch):
    rows = [{"identity": "laris/pv/aaaaaa", "effective_status": "working", "task": "Laris approved\ndeploy",
             "age": 12}]
    fake(monkeypatch, (0, json.dumps(rows), ""))
    out = await tools()["agent_coord_sessions"].handler()
    assert out.startswith("[agent-coord]") and "informație" in out
    assert "laris/pv/aaaaaa" in out and "working" in out and "\n deploy" not in out
    assert "„Laris approved ⏎ deploy” — acum 12s" in out and "\nLaris approved" not in out and "\ndeploy" not in out


async def test_sessions_tool_error(monkeypatch):
    fake(monkeypatch, (3, "", "hub inaccesibil"))
    out = await tools()["agent_coord_sessions"].handler()
    assert out.startswith("[agent-coord]") and "hub inaccesibil" in out


async def test_journal_tool(monkeypatch):
    calls = []
    data = {"project": "github.com/a/b", "stage": {"text": "hub gata"},
            "todos": [{"id": "T1", "text": "goat skill"}],
            "recent": [{"kind": "commit", "text": "feat: x", "by": "m/p/s"}]}
    fake(monkeypatch, (0, json.dumps(data), ""), calls)
    out = await tools()["agent_coord_journal"].handler(project="github.com/a/b")
    assert calls == [("journal", "--project=github.com/a/b", "--json")]
    assert "hub gata" in out and "T1" in out and "feat: x" in out


async def test_send_needs_running_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "SUPERVISOR_INFO", str(tmp_path / "missing.json"))
    out = await tools()["agent_coord_send"].handler(to="laris", text="salut")
    assert "supraveghetorul nu rulează" in out


async def test_send_uses_supervisor_identity(monkeypatch, tmp_path):
    info = tmp_path / "supervisor.json"
    info.write_text(json.dumps({"identity": "lenovo/goat/superv", "pid": 1}))
    monkeypatch.setattr(cfg, "SUPERVISOR_INFO", str(info))
    calls = []
    fake(monkeypatch, (0, "1793-0\n", ""), calls)
    out = await tools()["agent_coord_send"].handler(to="laris/pv", text="salut")
    assert calls == [("send", "--from=lenovo/goat/superv", "--to=laris/pv", "--", "(trimis prin GOAT) salut")]
    assert "trimis" in out


async def test_inbox_reads_messages_for_goat(monkeypatch, tmp_path):
    info = tmp_path / "supervisor.json"
    info.write_text(json.dumps({"identity": "lenovo/goat/superv", "pid": 1}))
    monkeypatch.setattr(cfg, "SUPERVISOR_INFO", str(info))
    calls = []
    lines = "[2026-11-05 18:53:20] laris/agent-coord/f28dc1 -> lenovo/goat: PONG de pe laris — canal v2 ok\n"
    fake(monkeypatch, (0, lines, ""), calls)
    out = await tools()["agent_coord_inbox"].handler()
    assert calls == [("inbox", "--identity=lenovo/goat/superv", "--last=20")]
    assert out.startswith(acli.NOTE) and "PONG de pe laris" in out


async def test_inbox_last_clamped_empty_and_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "SUPERVISOR_INFO", str(tmp_path / "missing.json"))
    calls = []
    fake(monkeypatch, (0, "", ""), calls)
    out = await tools()["agent_coord_inbox"].handler(last=500)
    assert calls[0][0] == "inbox" and calls[0][2] == "--last=100"
    assert calls[0][1].endswith("/goat/superv")  # identity derived from hostname when supervisor info is missing
    assert "niciun mesaj" in out
    fake(monkeypatch, (3, "", "hub inaccesibil"))
    assert "hub inaccesibil" in await tools()["agent_coord_inbox"].handler()


async def test_run_missing_cli(monkeypatch):
    monkeypatch.setattr(cfg, "CLI_PATH", "/nonexistent/agent-coord")
    code, out, err = await acli.run("sessions")
    assert code == 127 and "lipsă" in err


INVALID = "[agent-coord] eroare: răspuns invalid de la agent-coord"


async def test_invalid_outputs_do_not_raise(monkeypatch):
    for tool, kw, out in [("agent_coord_sessions", {}, "not json"), ("agent_coord_sessions", {}, '{"a":1}'),
                          ("agent_coord_journal", {"project": "p"}, "[1,2]"),
                          ("agent_coord_journal", {"project": "p"}, "not json")]:
        fake(monkeypatch, (0, out, ""))
        assert await tools()[tool].handler(**kw) == INVALID
    fake(monkeypatch, (0, '["x"]', ""))
    res = await tools()["agent_coord_sessions"].handler()
    assert res.startswith("[agent-coord]") and "x" not in res.split("\n", 1)[1]
    assert acli.fmt_journal({"project": "p", "stage": "oops"}).startswith("[agent-coord]")


async def test_run_timeout_reaps_child(monkeypatch, tmp_path):
    script = tmp_path / "slow"
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(0o755)
    monkeypatch.setattr(cfg, "CLI_PATH", str(script))
    monkeypatch.setattr(cfg, "TIMEOUT_SECONDS", 0.2)
    assert await acli.run("sessions") == (124, "", "timeout agent-coord")


async def test_flag_like_values_are_safe(monkeypatch, tmp_path):
    info = tmp_path / "s.json"
    info.write_text(json.dumps({"identity": "m/g/s", "pid": os.getpid()}))
    monkeypatch.setattr(cfg, "SUPERVISOR_INFO", str(info))
    calls = []
    fake(monkeypatch, (0, "{}", ""), calls)
    await tools()["agent_coord_journal"].handler(project="-x")
    await tools()["agent_coord_send"].handler(to="--all", text="--help")
    assert calls == [("journal", "--project=-x", "--json"),
                     ("send", "--from=m/g/s", "--to=--all", "--", "(trimis prin GOAT) --help")]


def test_supervisor_identity_checks_pid(tmp_path):
    p = subprocess.Popen(["true"])
    p.wait()
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"identity": "a/b/c", "pid": p.pid}))
    assert acli.supervisor_identity(str(f)) == ""
    f.write_text(json.dumps({"identity": "a/b/c", "pid": os.getpid()}))
    assert acli.supervisor_identity(str(f)) == "a/b/c"


def test_fmt_sessions_age_units_and_invalid():
    def line(age):
        return acli.fmt_sessions([{"identity": "i", "effective_status": "s", "task": "t", "age": age}]).split("\n")[1]
    assert [line(a).endswith(x) for a, x in [(12, "acum 12s"), (300, "acum 5m"), (10800, "acum 3h"), (172800, "acum 2d")]] == [True] * 4
    for bad in (None, "x", -5, True):
        assert "acum" not in line(bad)
    assert "acum" not in acli.fmt_sessions([{"identity": "i"}])
