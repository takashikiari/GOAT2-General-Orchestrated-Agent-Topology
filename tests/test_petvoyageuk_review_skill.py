import subprocess

import pytest

from tools.goat_skills import petvoyageuk_review as m


@pytest.fixture
def ssh(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_MIN_CALL_GAP_SECONDS", 0)
    monkeypatch.setattr(m.subprocess, "run",
                        lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0, "ok", ""))
    return seen


def handler():
    return m.build(object())[0].handler


@pytest.mark.parametrize("cmd", ["cat .env", "ls ~", "id", "git log; id", "git push", "rm -rf x",
                                 "git diff --no-index /etc/passwd x", "git log $(id)"])
async def test_refuses_before_ssh(ssh, cmd):
    assert (await handler()(command=cmd)).startswith("REFUZAT:")
    assert ssh == []


async def test_read_goes_through_quoted_in_repo(ssh):
    assert await handler()(command="git log -- *.py") == "ok"
    remote = ssh[0][-1]
    assert ssh[0][0] == "ssh" and "BatchMode=yes" in ssh[0]
    assert remote.startswith("GIT_PAGER=cat ") and "-C /home/laris/petvoyageuk" in remote and "'*.py'" in remote


async def test_bare_subcommand_accepted(ssh):
    assert await handler()(command="status") == "ok"
    assert ssh[0][-1].endswith(" status")
