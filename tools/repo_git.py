"""tools.repo_git — read-only git over every repository on lenovo (local) and laris (via SSH).

The guard lives HERE, client-side: the laris key is a normal login key (the old server-side wrapper that
restricted the PetVoyageUK reviewer key was lost with the Ubuntu box), so nothing reaches SSH unless it is
a single read-only git subcommand with no shell syntax and none of the options that write files or run
external programs. Locally git runs without a shell (argument list); on laris every argument is quoted.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

__all__ = ["validate", "discover", "run_readonly", "LARIS_HOST"]

LARIS_HOST = "laris@100.106.196.112"
SSH_KEY = "/home/lenovo/.ssh/id_ed25519"
TIMEOUT = 20
MAX_OUT = 12000
CACHE_TTL = 300

READ_ONLY = {"log", "show", "diff", "status", "branch", "grep", "blame", "ls-files", "ls-tree",
             "rev-parse", "shortlog", "describe", "rev-list", "cat-file", "remote", "tag", "reflog"}
# options that write a file, run an external program, read outside the repo or change refs/config
FORBIDDEN = re.compile(r"^(--output|--ext-diff|--no-index|--open-files-in-pager|--exec|--upload-pack|"
                       r"--receive-pack|--git-dir|--work-tree|--namespace|--config-env|--textconv|--filters|"
                       r"--contents|--edit-description|--set-upstream|--unset-upstream|--delete|--move|--copy|"
                       r"--force|--track|--no-track|--create-reflog)(=|$)|^-[a-zA-Z]*[OCcdDmMfu]$")
SHELL_CHARS = re.compile(r"[;&|`$<>\\\"'\n\r(){}]")  # ~ * ? stay allowed: no shell locally, quoted on laris
SKIP_DIRS = {"node_modules", ".cache", ".local", ".npm", ".cargo", "snap", ".claude", "worktrees", "venv", ".venv"}
# every call: no pager, no colour, no fsmonitor hook (could run a program on status)
GIT_SAFE = ["--no-pager", "-c", "core.pager=cat", "-c", "color.ui=never", "-c", "core.fsmonitor=false"]

_cache: dict = {"at": 0.0, "repos": {}}


def _shell_chars_reason(cmd: str) -> str:
    """Refusal text naming the forbidden characters found, plus how to write an OR without '|'."""
    found = sorted(set(SHELL_CHARS.findall(cmd)), key=cmd.index)
    shown = " ".join({"\n": "\\n", "\r": "\\r"}.get(c, c) for c in found)
    reason = (f"caracter interzis: {shown} — fără operatori de shell, ghilimele sau $, "
              "o singură comandă git simplă (argumentele se despart doar prin spații)")
    if "|" in found:
        reason += "; pentru SAU folosește -e A -e B (ex. 'grep -n -e clear_cache -e invalidate_cache -- *.py')"
    return reason


def validate(command: str) -> list[str]:
    """Argument list for a single read-only git command, or ValueError saying why it is refused."""
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("comandă goală")
    if SHELL_CHARS.search(cmd):
        raise ValueError(_shell_chars_reason(cmd))
    args = cmd.split()
    if args[0] == "git":
        args = args[1:]
    if not args or args[0].startswith("-"):
        raise ValueError("lipsește subcomanda git (ex. 'log --oneline -10')")
    sub, rest = args[0], args[1:]
    if sub not in READ_ONLY:
        raise ValueError(f"'{sub}' nu e permis — doar citire: {', '.join(sorted(READ_ONLY))}")
    for a in rest:
        if FORBIDDEN.match(a):
            raise ValueError(f"opțiunea '{a}' nu e permisă (scrie, rulează programe sau iese din repo)")
    if sub in ("branch", "tag") and any(not a.startswith("-") for a in rest):
        raise ValueError(f"'{sub}' doar pentru listare, fără nume (ex. '{sub} -a', '{sub} --contains=<hash>')")
    if sub == "remote" and rest not in ([], ["-v"]):
        raise ValueError("'remote' doar ca 'remote' sau 'remote -v'")
    if sub == "reflog" and rest[:1] in (["delete"], ["expire"]):
        raise ValueError("reflog doar pentru citire")
    if sub in ("log", "show", "diff"):
        rest = ["--no-ext-diff", *rest]
    return [sub, *rest]


def _local_repos(home: Path) -> dict[str, str]:
    out = {}
    for root, dirs, _files in os.walk(home):
        depth = Path(root).relative_to(home).parts
        if ".git" in dirs:
            rel = str(Path(root).relative_to(home)) or "~"
            out[f"lenovo:{rel}"] = root
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and len(depth) < 3]
    return out


def _laris_repos() -> dict[str, str]:
    find = ("find ~ -maxdepth 4 -name .git -type d -not -path '*/node_modules/*' -not -path '*/.claude/*' "
            "-not -path '*/.cache/*' -not -path '*/.local/*'")
    try:
        r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", LARIS_HOST, find],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        path = line.strip()[:-len("/.git")] if line.strip().endswith("/.git") else ""
        if path.startswith("/home/laris/"):
            out[f"laris:{path[len('/home/laris/'):]}"] = path
    return out


def discover(force: bool = False) -> dict[str, str]:
    """{'lenovo:workspace/goat2': '/home/lenovo/workspace/goat2', 'laris:petvoyageuk': ...}, cached 5 min."""
    if force or time.time() - _cache["at"] > CACHE_TTL or not _cache["repos"]:
        repos = _local_repos(Path.home())
        repos.update(_laris_repos())
        _cache.update(at=time.time(), repos=dict(sorted(repos.items())))
    return _cache["repos"]


def _resolve(repo: str) -> tuple[str, str]:
    repos = discover()
    if repo in repos:
        return repo, repos[repo]
    matches = [k for k in repos if k.split(":", 1)[1].split("/")[-1] == repo or k.endswith(":" + repo)]
    if len(matches) == 1:
        return matches[0], repos[matches[0]]
    if not matches:
        raise ValueError(f"repo necunoscut '{repo}' — vezi repo_list")
    raise ValueError(f"'{repo}' e ambiguu: {', '.join(matches)} — folosește numele complet")


def run_readonly(repo: str, command: str) -> str:
    try:
        name, path = _resolve(repo)
        args = validate(command)
    except ValueError as exc:
        return f"REFUZAT: {exc}"
    git = ["git", *GIT_SAFE, "-C", path, *args]
    env = dict(os.environ, GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0")
    try:
        if name.startswith("lenovo:"):
            r = subprocess.run(git, capture_output=True, text=True, timeout=TIMEOUT, env=env)
        else:
            remote = "GIT_PAGER=cat GIT_TERMINAL_PROMPT=0 " + " ".join(shlex.quote(a) for a in git)
            r = subprocess.run(["ssh", "-i", SSH_KEY, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", LARIS_HOST, remote],
                               capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"EROARE: timeout după {TIMEOUT}s"
    except OSError as exc:
        return f"EROARE: {exc}"
    out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.returncode and r.stderr else "")
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + f"\n… [tăiat la {MAX_OUT} caractere]"
    return out or "(fără ieșire)"
