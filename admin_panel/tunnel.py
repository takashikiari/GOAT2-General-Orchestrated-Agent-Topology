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

from admin_panel import tunnel_state
from admin_panel.admin_config import ADMIN_HOST, ADMIN_PORT
from admin_panel.tunnel_config import TUNNEL_ENABLED, TUNNEL_TIMEOUT_SECONDS
from config.admin_chat import load_admin_chat_id
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

log = get_logger(__name__)
__all__ = ["extract_tunnel_url", "start"]

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_EXIT_WAIT_S = 5.0  # after stderr EOF, how long to wait for cloudflared to exit before the finally kills it
# a clean exit (rc 0 / -15) is a service stop only if the bot's own shutdown cancels this task within this window
_SHUTDOWN_GRACE_S = 15.0


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
        tunnel_state.set_state("off", detail="tunnel disabled in config/admin_panel.toml")
        return
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id:
        log.warning("tunnel enabled but no admin_chat_id configured in goat2.toml — skipping")
        tunnel_state.set_state("off", detail="no admin_chat_id configured")
        return
    tunnel_state.set_state("starting")
    try:
        process = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://{ADMIN_HOST}:{ADMIN_PORT}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.warning("cloudflared not available, tunnel disabled: %s", exc)
        tunnel_state.set_state("unavailable", detail="cloudflared could not be started (is it in the bot's PATH?)")
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
                tunnel_state.set_state("down", detail=f"cloudflared reported no tunnel URL within {TUNNEL_TIMEOUT_SECONDS}s")
                return
            if not raw:
                try:  # bounded: stderr can close while the process lives on; the finally below kills it then
                    rc = await asyncio.wait_for(process.wait(), timeout=_EXIT_WAIT_S)
                except asyncio.TimeoutError:
                    rc = process.returncode
                    log.warning("cloudflared closed stderr but did not exit within %.0f s (rc=%s); killing it",
                                _EXIT_WAIT_S, rc)
                if rc is None:  # no exit yet (the timeout above, already logged): not an exit line
                    tunnel_state.set_state("down", detail=f"tunnel stopped (no exit within {_EXIT_WAIT_S:.0f} s)")
                    return
                tunnel_state.set_state("down", detail=f"cloudflared exited (rc={rc})")
                if rc in (0, -15):
                    # systemd stops the whole unit with SIGTERM, cloudflared included, and cloudflared exits (live:
                    # rc=0 on 17.11) about a second BEFORE the bot's own shutdown cancels this task. So a clean exit
                    # is a service stop only if that cancel comes: INFO then; if the bot keeps running, WARNING.
                    try:
                        await asyncio.sleep(_SHUTDOWN_GRACE_S)
                    except asyncio.CancelledError:
                        log.info("cloudflared stopped with the service (rc=%s)", rc)
                        raise
                    log.warning("cloudflared exited rc=%s while the bot kept running (tunnel down)", rc)
                    return
                log.warning("cloudflared exited rc=%s%s", rc, "" if url_found else " before reporting a tunnel URL")
                return
            if url_found:
                continue  # keep draining so the pipe never fills
            url = extract_tunnel_url(raw.decode(errors="replace"))
            if url is None:
                continue
            url_found = True
            log.info("admin panel tunnel ready: %s", url)
            tunnel_state.set_state("ready", url=url)
            try:
                await application.bot.set_chat_menu_button(
                    chat_id=int(admin_chat_id),
                    menu_button=MenuButtonWebApp(text="Admin Panel", web_app=WebAppInfo(url=url)),
                )
            except Exception as exc:  # noqa: BLE001 — menu button update is best-effort
                log.warning("failed to update chat menu button: %s", exc)
    finally:
        if tunnel_state.get_state()["status"] in ("starting", "ready"):
            # the tunnel is going away (cancelled or ended): its URL no longer works, so do not keep showing it
            tunnel_state.set_state("down", detail="tunnel stopped")
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
