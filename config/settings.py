"""
config.settings — single source of truth for GOAT 2.0 configuration.

All configurable values are read once from environment variables at import
time.  Every other module imports from here; nothing else reads os.environ
directly.  This keeps configuration changes in one place and makes the
dependency graph easy to audit.

Environment variables (with defaults):
    DEEPSEEK_API_KEY    — LLM provider API key (required; no hardcoded default)
    MODEL_NAME          — model identifier to call (default: "deepseek-chat")
    BASE_URL            — provider base URL (default: "https://api.deepseek.com")
    TEMPERATURE         — sampling temperature 0–2   (default: 0.5)
    MAX_TOKENS          — max tokens per LLM response (default: 2048)
    TIMEOUT_SECONDS     — HTTP timeout for LLM calls  (default: 30.0)
    TELEGRAM_BOT_TOKEN  — Telegram bot token from @BotFather (required for bot)
"""
from __future__ import annotations

import os

API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "deepseek-v4-flash")
BASE_URL: str = os.environ.get("BASE_URL", "https://api.deepseek.com")
TEMPERATURE: float = float(os.environ.get("TEMPERATURE", "0.5"))
MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "2048"))
TIMEOUT_SECONDS: float = float(os.environ.get("TIMEOUT_SECONDS", "30.0"))
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
