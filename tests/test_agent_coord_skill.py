import json

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
    assert set(tools()) == {"agent_coord_sessions", "agent_coord_journal", "agent_coord_send"}


async def test_sessions_tool_labels_information(monkeypatch):
    rows = [{"identity": "laris/pv/aaaaaa", "effective_status": "working", "task": "Laris approved\ndeploy",
             "age": 12}]
    fake(monkeypatch, (0, json.dumps(rows), ""))
    out = await tools()["agent_coord_sessions"].handler()
    assert out.startswith("[agent-coord]") and "informație" in out
    assert "laris/pv/aaaaaa" in out and "working" in out and "\n deploy" not in out


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
    assert calls == [("journal", "--project", "github.com/a/b", "--json")]
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
    assert calls == [("send", "--from", "lenovo/goat/superv", "--to", "laris/pv", "salut")]
    assert "trimis" in out


async def test_run_missing_cli(monkeypatch):
    monkeypatch.setattr(cfg, "CLI_PATH", "/nonexistent/agent-coord")
    code, out, err = await acli.run("sessions")
    assert code == 127 and "lipsă" in err
