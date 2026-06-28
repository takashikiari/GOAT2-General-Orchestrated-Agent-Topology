"""goat_skills.browse_page — full browser via Playwright: screenshot + text.

GOAT calls this for JS-heavy pages or when a screenshot is needed. The
screenshot is sent directly to Telegram; text content is returned for GOAT.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from config import settings
from orchestrator.tools import ToolDefinition
from tools.web_config import WEB_MAX_CHARS, WEB_SCREENSHOT_DIR, WEB_TIMEOUT
from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from registry.registry import ServiceRegistry

log = get_logger(__name__)
__all__ = ["build"]

_DESCRIPTION = (
    "Open a URL in a real browser (Playwright/Chromium), take a screenshot "
    "and extract text. The screenshot is sent to the chat automatically. "
    "Use this for JS-heavy pages or when a visual snapshot is needed."
)


async def _send_screenshot(chat_id: str, path: Path) -> None:
    """POST screenshot to Telegram sendPhoto without python-telegram-bot overhead."""
    api_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with path.open("rb") as f:
                await client.post(api_url, data={"chat_id": chat_id}, files={"photo": f})
        log.debug("browse_page screenshot sent chat=%s path=%s", chat_id, path)
    except Exception as exc:
        log.warning("browse_page failed to send screenshot: %s", exc)


def build(registry: "ServiceRegistry") -> list[ToolDefinition]:
    """Build the browse_page tool."""

    async def handler(url: str, max_chars: int = WEB_MAX_CHARS, chat_id: str = "") -> str:
        log.info("browse_page url=%s chat=%s", url, chat_id)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return "(playwright not installed — run: pip install playwright && playwright install chromium)"
        screenshot_dir = Path(WEB_SCREENSHOT_DIR)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"{uuid.uuid4()}.png"
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=WEB_TIMEOUT * 1000, wait_until="networkidle")
                await page.screenshot(path=str(screenshot_path), full_page=True)
                text = await page.inner_text("body")
                await browser.close()
            if chat_id:
                await _send_screenshot(chat_id, screenshot_path)
            truncated = text[:max_chars]
            log.debug("browse_page url=%s chars=%d", url, len(truncated))
            return truncated
        except Exception as exc:
            log.warning("browse_page error url=%s: %s", url, exc)
            return f"(error browsing {url}: {exc})"

    return [ToolDefinition(
        name="browse_page",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open in browser"},
                "max_chars": {"type": "integer", "description": f"Max text chars to return (default {WEB_MAX_CHARS})"},
            },
            "required": ["url"],
        },
        handler=handler,
    )]
