"""goat_skills.agent_coord_status — ask the agent-coord hub about Claude sessions and project journals,
or send a message to a session. Shells out to the agent-coord CLI; hot-reloaded, no bot restart."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING

from orchestrator.tools import ToolDefinition
from tools import agent_coord_cli as acli
from tools import agent_coord_skill_config as cfg

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

__all__ = ["build"]

SEND_PREFIX = "(trimis prin GOAT) "  # an outgoing message can never pass as an approval from Laris
INVALID = "[agent-coord] eroare: răspuns invalid de la agent-coord"


def _err(code: int, err: str) -> str:
    return f"[agent-coord] eroare ({code}): {acli.one_line(err.strip(), 300)}"


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    async def sessions(chat_id: str = "") -> str:
        code, out, err = await acli.run("sessions", "--json")
        if code:
            return _err(code, err)
        rows = acli.parse_json(out, list)
        return INVALID if rows is None else acli.fmt_sessions(rows)

    async def journal(project: str, chat_id: str = "") -> str:
        code, out, err = await acli.run("journal", f"--project={project}", "--json")
        if code:
            return _err(code, err)
        data = acli.parse_json(out, dict)
        return INVALID if data is None else acli.fmt_journal(data)

    async def send(to: str, text: str, chat_id: str = "") -> str:
        me = acli.supervisor_identity(cfg.SUPERVISOR_INFO)
        if not me:
            return "[agent-coord] supraveghetorul nu rulează — nu pot trimite (pornește `agent-coord supervise`)"
        code, _, err = await acli.run("send", f"--from={me}", f"--to={to}", "--", SEND_PREFIX + text)
        return _err(code, err) if code else f"[agent-coord] trimis către {acli.one_line(to)} (de la {me})"

    async def inbox(last: int = 20, chat_id: str = "") -> str:
        """Read-only: messages for GOAT (to lenovo/goat, lenovo, all); never acks (the supervisor forwards them)."""
        me = acli.supervisor_identity(cfg.SUPERVISOR_INFO) or f"{socket.gethostname().lower()}/goat/superv"
        try:
            n = max(1, min(int(last), 100))
        except (TypeError, ValueError):
            n = 20
        code, out, err = await acli.run("inbox", f"--identity={me}", f"--last={n}")
        if code:
            return _err(code, err)
        body = out.strip() or "(niciun mesaj)"
        return f"{acli.NOTE}\n{body}"[:3000]

    return [
        ToolDefinition(name="agent_coord_inbox",
                       description="Mesajele din agent-coord pentru GOAT (de la sesiunile Claude, ex. laris), "
                                   "cele mai recente ultimele. last = câte (1-100). Doar citire.",
                       parameters={"type": "object", "properties": {"last": {"type": "integer"}}},
                       handler=inbox),
        ToolDefinition(name="agent_coord_sessions",
                       description="Lista sesiunilor Claude Code din hub-ul agent-coord: status și task.",
                       parameters={"type": "object", "properties": {}}, handler=sessions),
        ToolDefinition(name="agent_coord_journal",
                       description="Jurnalul unui proiect agent-coord (stadiu, todo deschise, recente). "
                                   "project = cheia, ex. github.com/takashikiari/agent-coord.",
                       parameters={"type": "object", "properties": {"project": {"type": "string"}},
                                   "required": ["project"]}, handler=journal),
        ToolDefinition(name="agent_coord_send",
                       description="Trimite un mesaj unei sesiuni Claude prin agent-coord: la cererea lui Laris sau ca "
                                   "răspuns la un mesaj primit pe canal (nu pentru push/merge/deploy fără OK-ul "
                                   "lui Laris). to = identitate, machine/proiect, mașină sau all.",
                       parameters={"type": "object", "properties": {"to": {"type": "string"},
                                                                    "text": {"type": "string"}},
                                   "required": ["to", "text"]}, handler=send),
    ]
