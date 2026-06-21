"""
telegram_interface.__main__ — run the GOAT 2.0 Telegram bot as a module.

Usage:
    python3 -m telegram_interface
"""
from __future__ import annotations

from telegram_interface.bot import run_polling

if __name__ == "__main__":
    run_polling()
