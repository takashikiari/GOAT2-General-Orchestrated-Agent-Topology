# GOAT 2.0 — Setup

Three steps to get started.

---

## Requirements

- Python 3.11 or newer — [python.org](https://python.org)
- Git — [git-scm.com](https://git-scm.com)
- A Telegram account — you'll create a bot in step 2

---

## Step 1 — Clone and run

```bash
git clone https://github.com/takashikiari/GOAT2-General-Orchestrated-Agent-Topology.git
cd GOAT2-General-Orchestrated-Agent-Topology
./run.sh
```

On Windows:
```
run.bat
```

The first time you run this, the setup wizard starts automatically.

---

## Step 2 — Follow the wizard

The wizard will ask you to:

1. **Choose an AI provider** (DeepSeek, OpenAI, Anthropic, Groq, Ollama, …)
2. **Paste your API key** — get one from the provider's website (links shown in the wizard)
3. **Choose optional services** — Redis and ChromaDB extend GOAT's memory. Skip them if you don't have them installed.
4. **Create a Telegram bot** — open Telegram, message [@BotFather](https://t.me/BotFather), type `/newbot`, follow the steps. Paste the token when asked.

That's it. The wizard generates two files:
- `goat2.toml` — your configuration
- `.env` — your private keys (never share this file)

---

## Updating

When a new version is available, GOAT notifies you in Telegram.  
To update, send `/update` to your bot and confirm.

To update manually:
```bash
python3 setup/updater.py
```

---

## Rollback

If something breaks after an update:
```bash
python3 setup/rollback.py
```

Pick the version you want to go back to.

---

## Reconfigure

To change providers, API keys, or services:
```bash
python3 setup/wizard.py --reconfigure
```

---

## Troubleshooting

**"Redis not reachable"** — Install Redis or set `services.redis.enabled = false` in `goat2.toml`.  
**"ChromaDB not installed"** — Run `pip install chromadb` or set `services.chroma.enabled = false`.  
**Bot doesn't respond** — Check your `TELEGRAM_TOKEN` in `.env`.  
**Wrong AI responses** — Check your API key in `.env` and that `providers.default` in `goat2.toml` matches a provider with `enabled = true`.
