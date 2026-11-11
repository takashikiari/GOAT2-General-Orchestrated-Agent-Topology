"""setup.live_checks — the wizard tests each API key and the Telegram token for real, before writing them.

Every provider GOAT runs (DeepSeek, OpenAI, Groq) is OpenAI-compatible, so a key is tested with the cheapest call there
is: GET {base_url}/models (no tokens spent). The Telegram token is tested with getMe, which also names the bot. Plain
urllib, no third-party imports; the URLs can be pointed elsewhere, which is how the tests run against a local server.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from setup.service_steps import Outcome

__all__ = ["check_api_key", "check_telegram_token", "TELEGRAM_API"]

TELEGRAM_API = "https://api.telegram.org"
_TIMEOUT = 10.0


def _get(url: str, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "goat-setup", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def check_api_key(base_url: str, key: str) -> Outcome:
    """The key works with this provider (GET /models answers 200)."""
    if not key.strip():
        return Outcome(False, "no key entered", "Paste the key from the provider's site")
    try:
        status, _ = _get(base_url.rstrip("/") + "/models", {"Authorization": f"Bearer {key.strip()}"})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Outcome(False, f"the provider could not be reached ({type(exc).__name__})",
                       "Check the internet connection, then retry")
    if status == 200:
        return Outcome(True, "the key works")
    if status in (401, 403):
        return Outcome(False, "the key does not answer: the provider refused it (wrong or revoked)",
                       "Copy the key again from the provider's site (no spaces) and re-enter it")
    if status == 402:
        return Outcome(False, "the provider says the account has no credit", "Add credit on the provider's site, then retry")
    return Outcome(False, f"the provider answered HTTP {status}", "Retry in a moment")


def check_telegram_token(token: str, api: str = TELEGRAM_API) -> Outcome:
    """The bot token works (getMe), and which bot it is."""
    if ":" not in token.strip():
        return Outcome(False, "that does not look like a bot token (it has the form 123456:ABC...)",
                       "Copy the token @BotFather sent you")
    try:
        status, body = _get(f"{api.rstrip('/')}/bot{token.strip()}/getMe")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Outcome(False, f"Telegram could not be reached ({type(exc).__name__})", "Check the internet connection, then retry")
    if status == 200 and body.get("ok"):
        bot = body.get("result") or {}
        return Outcome(True, f"bot @{bot.get('username', '?')} ({bot.get('first_name', '')}) answers")
    return Outcome(False, "Telegram refused the token", "Copy the token @BotFather sent you again (or /token in BotFather)")
