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
MAX_INBOX_CHARS = 3000
TRUNC_MARK = "[...mesaje mai vechi tăiate...]\n"


def _err(code: int, err: str) -> str:
    return f"[agent-coord] eroare ({code}): {acli.one_line(err.strip(), 300)}"


def _keep_tail(body: str, limit: int) -> str:
    """Truncate from the HEAD, never the tail: the CLI prints oldest -> newest, so the newest
    message is the LAST line. Cutting the head keeps what is new; cutting the tail dropped it."""
    if limit <= 0 or len(body) <= limit:
        return body
    budget = limit - len(TRUNC_MARK)
    if budget <= 0:
        return TRUNC_MARK[:limit]
    cut = body[-budget:]
    nl = cut.find("\n")  # never start mid-line
    if nl != -1:
        cut = cut[nl + 1:]
    return TRUNC_MARK + cut


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
        head = f"{acli.NOTE}\n"
        return head + _keep_tail(body, MAX_INBOX_CHARS - len(head))

    async def events(last: int = 30, identity: str = "", chat_id: str = "") -> str:
        """Read-only live tracking: newest status/task/tool/commit events from the hub, newest last."""
        try:
            n = max(1, min(int(last), 200))
        except (TypeError, ValueError):
            n = 30
        args = ["events", f"--last={n}"] + ([f"--identity={identity}"] if identity else [])
        code, out, err = await acli.run(*args)
        if code:
            return _err(code, err)
        body = out.strip() or "(niciun eveniment)"
        head = f"{acli.NOTE}\n"
        return head + _keep_tail(body, MAX_INBOX_CHARS - len(head))

    return [
        ToolDefinition(name="agent_coord_events",
                       description="Tracking live agent-coord: ultimele evenimente ale sesiunilor Claude (status, "
                                   "task, unealta folosită, commit), cele mai recente ultimele. last = câte (1-200), "
                                   "identity = opțional, doar o sesiune. Doar citire.",
                       parameters={"type": "object", "properties": {"last": {"type": "integer"},
                                                                    "identity": {"type": "string"}}},
                       handler=events),
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
