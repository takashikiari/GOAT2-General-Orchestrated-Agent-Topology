"""goat_skills.repo_git — read-only git over every repository on lenovo and laris (GOAT-only, hot-reloaded).

repo_list shows the repositories found (lenovo:<path>, laris:<path>); repo_git runs ONE read-only git
command in one of them. The guard (subcommand allowlist, forbidden write/exec options, no shell syntax)
is enforced in tools/repo_git.py before anything runs.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools import repo_git as rg
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    async def repo_list(refresh: bool = False, chat_id: str = "") -> str:
        repos = await asyncio.to_thread(rg.discover, bool(refresh))
        if not repos:
            return "(niciun repo găsit)"
        return "Repo-uri (doar citire):\n" + "\n".join(f"- {k}" for k in repos)

    async def repo_git(repo: str, command: str, chat_id: str = "") -> str:
        log.info("repo_git: repo=%r command=%r", repo[:80], command[:200])
        return await asyncio.to_thread(rg.run_readonly, repo, command)

    return [
        ToolDefinition(
            name="repo_list",
            description="Lista tuturor repo-urilor git de pe lenovo și laris pe care le poți citi cu repo_git "
                        "(nume de forma 'lenovo:workspace/goat2', 'laris:petvoyageuk'). refresh=true reîncarcă lista.",
            parameters={"type": "object", "properties": {"refresh": {"type": "boolean"}}},
            handler=repo_list,
        ),
        ToolDefinition(
            name="repo_git",
            description=(
                "Rulează O comandă git DOAR DE CITIRE în orice repo de pe lenovo sau laris (vezi repo_list). "
                "Permise: log, show, diff, status, branch (listare), grep, blame, ls-files, ls-tree, rev-parse, "
                "shortlog, describe, rev-list, cat-file, remote -v, tag (listare), reflog. Fără commit/push/checkout, "
                "fără operatori de shell (&&, ;, |), fără ghilimele — comenzile refuzate întorc 'REFUZAT: motiv'. "
                "repo = numele din repo_list sau doar ultimul segment dacă e unic (ex. 'petvoyageuk')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "ex. 'laris:petvoyageuk' sau 'lenovo:workspace/goat2'"},
                    "command": {"type": "string", "description": "ex. 'log --oneline -10', 'show HEAD~1 --stat'"},
                },
                "required": ["repo", "command"],
            },
            handler=repo_git,
        ),
    ]
