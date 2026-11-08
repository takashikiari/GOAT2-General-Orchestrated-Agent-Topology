"""admin_panel.tunnel_state — what the panel knows about its own Cloudflare tunnel (shown in the dashboard).

``admin_panel.tunnel`` records every step here: the quick-tunnel URL is new on every bot start (and a tunnel that
stopped has no usable URL), so the dashboard reads this instead of anyone copying the link out of the log. No heavy
imports on purpose: the route and the tests use it without pulling in telegram.
"""
from __future__ import annotations

import time

__all__ = ["STATUSES", "set_state", "get_state"]

# off = disabled or not configured, starting = cloudflared launched, ready = URL known and in use,
# down = was starting/ready and stopped, unavailable = cloudflared could not be launched
STATUSES = ("off", "starting", "ready", "down", "unavailable")

_state: dict = {"status": "off", "url": None, "since": None, "detail": ""}


def set_state(status: str, *, url: str | None = None, detail: str = "") -> None:
    """Replace the tunnel state. ``url`` is only meaningful for ``ready``; every other status clears it."""
    if status not in STATUSES:
        raise ValueError(f"unknown tunnel status {status!r}")
    _state.update(status=status, url=url if status == "ready" else None, detail=detail, since=time.time())


def get_state() -> dict:
    """A copy of the current state: ``status``, ``url``, ``since`` (epoch seconds) and ``detail``."""
    return dict(_state)
