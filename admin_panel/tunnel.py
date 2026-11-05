"""admin_panel.tunnel — Cloudflare Quick Tunnel launcher + chat menu button updater.

Exposes the admin panel (127.0.0.1, see admin_panel.server) as a Telegram
Mini App: launches `cloudflared tunnel --url ...` as a subprocess, parses
the auto-assigned *.trycloudflare.com URL from its output, then updates the
bot's chat menu button to open that URL. Opt-in via [tunnel] enabled in
config/admin_panel.toml; every failure degrades to a logged WARNING — the
tunnel is a convenience layered on top of the panel, never a hard
dependency of bot startup.
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from telegram import MenuButtonWebApp, WebAppInfo

from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.tunnel_config import TUNNEL_ENABLED, TUNNEL_TIMEOUT_SECONDS
from config.admin_chat import load_admin_chat_id
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

log = get_logger(__name__)
__all__ = ["extract_tunnel_url", "start"]

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_tunnel_url(line: str) -> str | None:
    """Return the *.trycloudflare.com URL in ``line``, or None if absent."""
    match = _TUNNEL_URL_RE.search(line)
    return match.group(0) if match else None


async def start(application: "Application") -> None:
    """Launch the Cloudflare Quick Tunnel and keep it running. Never raises.

    Keeps draining cloudflared's stderr for the life of the tunnel process,
    not just until the URL is found — otherwise the pipe buffer fills once
    cloudflared's own periodic log lines exceed it, blocking the tunnel.
    """
    if not TUNNEL_ENABLED:
        return
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id:
        log.warning("tunnel enabled but no admin_chat_id configured in goat2.toml — skipping")
        return
    try:
        process = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://{ADMIN_HOST}:{ADMIN_PORT}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.warning("cloudflared not available, tunnel disabled: %s", exc)
        return

    assert process.stderr is not None
    try:
        url_found = False
        while True:
            try:
                raw = await asyncio.wait_for(
                    process.stderr.readline(),
                    timeout=TUNNEL_TIMEOUT_SECONDS if not url_found else None,
                )
            except (asyncio.TimeoutError, asyncio.LimitOverrunError) as exc:
                log.warning(
                    "cloudflared did not report a tunnel URL within %ds (%s)",
                    TUNNEL_TIMEOUT_SECONDS, exc,
                )
                return
            if not raw:
                log.warning("cloudflared exited%s", "" if url_found else " before reporting a tunnel URL")
                return
            if url_found:
                continue  # keep draining so the pipe never fills
            url = extract_tunnel_url(raw.decode(errors="replace"))
            if url is None:
                continue
            url_found = True
            log.info("admin panel tunnel ready: %s", url)
            try:
                await application.bot.set_chat_menu_button(
                    chat_id=int(admin_chat_id),
                    menu_button=MenuButtonWebApp(text="Admin Panel", web_app=WebAppInfo(url=url)),
                )
            except Exception as exc:  # noqa: BLE001 — menu button update is best-effort
                log.warning("failed to update chat menu button: %s", exc)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            else:
                # Reap the process so asyncio can close its subprocess
                # transport now, while the event loop is still open — skipping
                # this leaves the transport to close itself via __del__ after
                # the loop closes, logging a spurious "Event loop is closed".
                await process.wait()
