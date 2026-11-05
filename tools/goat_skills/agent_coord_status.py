"""goat_skills.agent_coord_status — ask the agent-coord hub about Claude sessions and project journals,
or send a message to a session. Shells out to the agent-coord CLI; hot-reloaded, no bot restart."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools import agent_coord_cli as acli
from tools import agent_coord_skill_config as cfg

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]


def _err(code: int, err: str) -> str:
    return f"[agent-coord] eroare ({code}): {acli.one_line(err.strip(), 300)}"


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    async def sessions(chat_id: str = "") -> str:
        code, out, err = await acli.run("sessions", "--json")
        if code:
            return _err(code, err)
        return acli.fmt_sessions(json.loads(out or "[]"))

    async def journal(project: str, chat_id: str = "") -> str:
        code, out, err = await acli.run("journal", "--project", project, "--json")
        if code:
            return _err(code, err)
        return acli.fmt_journal(json.loads(out or "{}"))

    async def send(to: str, text: str, chat_id: str = "") -> str:
        me = acli.supervisor_identity(cfg.SUPERVISOR_INFO)
        if not me:
            return "[agent-coord] supraveghetorul nu rulează — nu pot trimite (pornește `agent-coord supervise`)"
        code, _, err = await acli.run("send", "--from", me, "--to", to, text)
        return _err(code, err) if code else f"[agent-coord] trimis către {acli.one_line(to)} (de la {me})"

    return [
        ToolDefinition(name="agent_coord_sessions",
                       description="Lista sesiunilor Claude Code din hub-ul agent-coord: status și task.",
                       parameters={"type": "object", "properties": {}}, handler=sessions),
        ToolDefinition(name="agent_coord_journal",
                       description="Jurnalul unui proiect agent-coord (stadiu, todo deschise, recente). "
                                   "project = cheia, ex. github.com/takashikiari/agent-coord.",
                       parameters={"type": "object", "properties": {"project": {"type": "string"}},
                                   "required": ["project"]}, handler=journal),
        ToolDefinition(name="agent_coord_send",
                       description="Trimite un mesaj unei sesiuni Claude prin agent-coord (doar la cererea "
                                   "utilizatorului). to = identitate, machine/proiect, mașină sau all.",
                       parameters={"type": "object", "properties": {"to": {"type": "string"},
                                                                    "text": {"type": "string"}},
                                   "required": ["to", "text"]}, handler=send),
    ]
