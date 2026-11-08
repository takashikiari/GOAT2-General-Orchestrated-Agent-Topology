"""admin_panel.coord_feed — GOAT's agent-coord digest turns, told apart from the real conversation.

GOAT's coord poller (telegram_interface._coord_poller) feeds the messages other sessions send it through its own
orchestrator as one "digest" user message, and GOAT answers it. Both land in the admin chat's stored conversation,
mixed with what Laris says. The chat page must show only the real conversation; the agents page shows the rest.
The digest is recognised by the fixed prefix the poller puts on it (``CHANNEL_DIGEST_PREFIX``) and its lines are
read back with a pattern that follows ``telegram_interface._coord_poller._line`` (a test keeps the two in step).
"""
from __future__ import annotations

import re

from memory.temporal_route import CHANNEL_DIGEST_PREFIX

__all__ = ["is_digest", "parse_digest", "split_conversation"]

# "[from -> to][ [alertă supraveghetor]][ ⚠ origine reală: host] „text”", one message per line
_LINE = re.compile(r"^\[(?P<frm>[^\]]*?) -> (?P<to>[^\]]*?)\](?P<mid>[^„\n]*)„(?P<text>.*)”\s*$")
_ORIGIN = re.compile(r"⚠ origine reală:\s*(?P<host>.+?)\s*$")
_ALERT = "[alertă supraveghetor]"
_TOOLS_HEAD = "[Tool calls]\n"


def _content(entry: dict) -> str:
    value = entry.get("content")
    return value if isinstance(value, str) else ""


def is_digest(entry: dict) -> bool:
    """True for the user entry GOAT's coord poller writes (not for anything a person typed)."""
    return entry.get("role") == "user" and _content(entry).startswith(CHANNEL_DIGEST_PREFIX)


def parse_digest(content: str) -> list[dict]:
    """The individual messages of a digest: from, to, text, alert (supervisor alert) and origin_warning."""
    out: list[dict] = []
    for block in (content or "").split("\n\n"):
        match = _LINE.match(block.strip())
        if not match:
            continue
        mid = match.group("mid")
        origin = _ORIGIN.search(mid)
        out.append({
            "from": match.group("frm"),
            "to": match.group("to"),
            "text": match.group("text"),
            "alert": _ALERT in mid,
            "origin_warning": origin.group("host") if origin else None,
        })
    return out


def _split_tools(content: str) -> tuple[str | None, str]:
    """GOAT's stored replies start with "[Tool calls]\\n<summary>\\n\\n<reply>" when tools ran."""
    if content.startswith(_TOOLS_HEAD):
        end = content.find("\n\n")
        if end > 0:
            return content[len(_TOOLS_HEAD):end], content[end + 2:]
    return None, content


def split_conversation(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(chat entries, agent turns). A digest and GOAT's reply right after it leave the chat and become one agent turn."""
    chat: list[dict] = []
    agent: list[dict] = []
    i = 0
    while i < len(messages):
        entry = messages[i]
        if not is_digest(entry):
            chat.append(entry)
            i += 1
            continue
        reply = messages[i + 1] if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant" else None
        tools, text = _split_tools(_content(reply)) if reply is not None else (None, "")
        agent.append({
            "timestamp": entry.get("timestamp", 0),
            "reply_timestamp": reply.get("timestamp", 0) if reply is not None else None,
            "messages": parse_digest(_content(entry)),
            "goat_reply": (text or None) if reply is not None else None,
            "tools": tools,
        })
        i += 2 if reply is not None else 1
    return chat, agent
