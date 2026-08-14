"""scripts.gen_dev_init_data — generate a signed Telegram initData for local frontend dev.

Writes admin_panel/frontend/.env.local (gitignored) with a real, validly-signed
initData string for the configured admin_chat_id, so `npm run dev` can hit the
real backend without a live Telegram client or Cloudflare tunnel. Reuses the
exact HMAC algorithm admin_panel.telegram_auth.verify_init_data checks against
— nothing here is a separate implementation to keep in sync.

Run manually, on demand, whenever the previous token expires (auth_date older
than [auth] max_age_seconds, default 86400s) or hasn't been generated yet:

    python3 -m scripts.gen_dev_init_data
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from config import settings
from config.admin_chat import load_admin_chat_id

_ENV_LOCAL_PATH = Path(__file__).parent.parent / "admin_panel" / "frontend" / ".env.local"

__all__ = ["generate_dev_init_data"]


def _sign(fields: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def generate_dev_init_data() -> str:
    """Build a signed initData string for the configured admin_chat_id."""
    admin_chat_id = load_admin_chat_id()
    if not admin_chat_id:
        raise SystemExit("admin_chat_id not configured in goat2.toml — set it before running this script")
    if not settings.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env — set it before running this script")
    fields = {
        "user": json.dumps({"id": int(admin_chat_id), "first_name": "Dev"}),
        "auth_date": str(int(time.time())),
    }
    fields["hash"] = _sign(fields, settings.TELEGRAM_BOT_TOKEN)
    return urlencode(fields)


def main() -> None:
    init_data = generate_dev_init_data()
    _ENV_LOCAL_PATH.write_text(f"VITE_DEV_INIT_DATA={init_data}\n")
    print(f"wrote {_ENV_LOCAL_PATH}")


if __name__ == "__main__":
    main()
