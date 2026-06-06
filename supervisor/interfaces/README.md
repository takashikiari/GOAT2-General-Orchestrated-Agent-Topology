# supervisor/interfaces/ — GOAT 2.0 Channel Interfaces

Channel adapters that wrap `GoatSupervisor.run()` for external messaging platforms.
Each adapter maintains one `GoatSupervisor` instance per conversation so history is isolated.

## Usage

```bash
# Telegram — token read from config/goat.toml [channels] telegram_token
python -m supervisor.interfaces.telegram_bot
```

## Module map

| File | Responsibility |
|------|----------------|
| `telegram_bot.py` | Telegram adapter — per-chat `GoatSupervisor`; long-polling via `python-telegram-bot` |

## Token configuration

`config/goat.toml`:

```toml
[channels]
telegram_token   = "<bot-token>"
telegram_enabled = true
```

## Adding a new channel

1. Create `supervisor/interfaces/<channel>_bot.py` (≤ 90 lines, single responsibility).
2. Export `build_app` / `main` and add them to `__init__.py`.
3. Update this README and `docs/architecture.md`.
