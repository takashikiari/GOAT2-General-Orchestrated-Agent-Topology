"""orchestrator.tool_args — a tool's arguments checked against its own schema before the handler runs.

In the DSML path (deepseek-v4-flash writing tool calls as text) no schema is enforced, so the model can invent
parameter names: on 2026-11-12 it called read_file with offset_start / offset_end, the handler raised
"unexpected keyword argument" and GOAT could only report that the check failed. Instead the model now gets an error
that names the wrong parameters and lists the real ones, so it can call the tool again correctly.
"""
from __future__ import annotations

__all__ = ["argument_error", "INJECTED"]

INJECTED = frozenset({"chat_id"})  # added by the orchestrator, never by the model


def _describe(params: dict) -> str:
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    return ", ".join(f"{name} (required)" if name in required else name for name in props) or "no parameters"


def argument_error(tool, args: dict) -> str | None:
    """None when ``args`` fit the tool's schema; else a message for the model naming what is wrong and what it accepts.
    A schema without ``properties``, or with none listed, is not checked (nothing to check against: such tools may
    take free-form arguments)."""
    params = getattr(tool, "parameters", None) or {}
    props = params.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    given = {k for k in args if k not in INJECTED}
    unknown = sorted(given - set(props))
    missing = sorted(set(params.get("required") or []) - given)
    if not unknown and not missing:
        return None
    parts = []
    if unknown:
        parts.append(f"unknown parameter(s): {', '.join(unknown)}")
    if missing:
        parts.append(f"missing required parameter(s): {', '.join(missing)}")
    return f"{tool.name}: {'; '.join(parts)}. It accepts: {_describe(params)}. Call it again with those names."
