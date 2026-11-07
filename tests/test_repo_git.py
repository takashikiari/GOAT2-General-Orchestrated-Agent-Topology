import subprocess

import pytest

from tools import repo_git as rg
from tools.goat_skills import repo_git as skill


@pytest.mark.parametrize("cmd", [
    "log --oneline -5", "git log -3 --stat", "show HEAD~1 --stat", "diff HEAD~2..HEAD", "status",
    "branch -a", "grep -n TODO", "blame README.md", "ls-files", "rev-parse HEAD", "remote -v",
    "tag -l", "reflog -5", "log -- *.py", "ls-tree HEAD~1",
])
def test_validate_allows_reads(cmd):
    args = rg.validate(cmd)
    assert args[0] != "git" and args[0] in rg.READ_ONLY


@pytest.mark.parametrize("cmd", [
    "", "push", "commit -m x", "checkout main", "reset --hard", "log --output=/tmp/x", "diff --no-index /etc/passwd x",
    "diff --ext-diff", "branch new", "branch -D main", "branch -m a", "tag v1", "remote add x y", "blame --contents=/etc/x f",
    "grep -O ls", "log; rm -rf ~", "log && push", "log | sh", "log $(id)", "log `id`", "show 'a b'", "log > out",
    "-c core.pager=sh log", "log --git-dir=/x", "reflog delete HEAD@{0}", "reflog expire --all", "config user.name",
    "log --exec=sh", "status\nrm x",
])
def test_validate_refuses(cmd):
    with pytest.raises(ValueError):
        rg.validate(cmd)


def test_log_show_diff_get_no_ext_diff():
    assert rg.validate("diff HEAD")[:2] == ["diff", "--no-ext-diff"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "work" / "proj"
    r.mkdir(parents=True)
    git = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "a.txt").write_text("hello\n")
    subprocess.run([*git, "add", "a.txt"], check=True)
    subprocess.run([*git, "commit", "-qm", "first"], check=True)
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "node_modules" / "dep")], check=True)
    monkeypatch.setattr(rg, "_laris_repos", lambda: {})
    monkeypatch.setattr(rg.Path, "home", classmethod(lambda cls: tmp_path))
    rg._cache.update(at=0.0, repos={})
    return r


def test_discover_names_and_skips(repo):
    repos = rg.discover(force=True)
    assert repos == {"lenovo:work/proj": str(repo)}


def test_run_readonly_local(repo):
    assert "first" in rg.run_readonly("proj", "log --oneline")
    assert "first" in rg.run_readonly("lenovo:work/proj", "git log -1")
    assert rg.run_readonly("proj", "push").startswith("REFUZAT:")
    assert rg.run_readonly("nope", "log").startswith("REFUZAT:")


def test_run_readonly_never_uses_shell(repo, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(args, **kw):
        seen.append((args, kw.get("shell", False)))
        return real(args, **kw)

    monkeypatch.setattr(rg.subprocess, "run", spy)
    rg.run_readonly("proj", "log -1")
    assert seen and all(isinstance(a, list) and not sh for a, sh in seen)
    assert rg.GIT_SAFE[0] in seen[0][0]


def test_laris_command_is_quoted(monkeypatch):
    seen = []
    monkeypatch.setattr(rg, "_resolve", lambda r: ("laris:petvoyageuk", "/home/laris/petvoyageuk"))
    monkeypatch.setattr(rg.subprocess, "run",
                        lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0, "ok", ""))
    assert rg.run_readonly("petvoyageuk", "log -- *.py") == "ok"
    assert "'*.py'" in seen[0][-1] and seen[0][0] == "ssh"


def test_output_capped(repo, monkeypatch):
    monkeypatch.setattr(rg, "MAX_OUT", 10)
    assert "tăiat" in rg.run_readonly("proj", "log")


async def test_skill_tools(repo):
    tools = {t.name: t for t in skill.build(object())}
    assert set(tools) == {"repo_list", "repo_git"}
    assert "lenovo:work/proj" in await tools["repo_list"].handler()
    assert "first" in await tools["repo_git"].handler(repo="proj", command="log --oneline")
