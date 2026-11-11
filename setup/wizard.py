"""setup.wizard — interactive setup: from a fresh clone to a running bot, without editing any file by hand.

It runs FIRST (run.sh starts it on the first run and on --reconfigure, before any service check) and turns every check
into a step that can be fixed and retried instead of an exit:
  1. the AI provider: step-by-step instructions, the key tested live (re-enter it until it works)
  2. Telegram: the bot token tested with getMe, the admin chat id
  3. services: Redis (required; start command for this machine, address, real PING, retry), ChromaDB (required;
     embedded, so only the package and its data folders), Letta (optional)
  4. the files: .env (0600, with a backup), config/memory.toml (the addresses, with a backup), goat2.toml
  5. a summary of what was written, then ./run.sh
The checks themselves live in modules without third-party imports (service_steps, live_checks, memory_toml,
env_writer, wizard_files), which is what the tests exercise.

Usage:
    python3 setup/wizard.py
    python3 setup/wizard.py --reconfigure   # run again; every current value is the default
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_MISSING = []
try:
    import questionary
except ImportError:
    _MISSING.append("questionary")
try:
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    _MISSING.append("rich")
if _MISSING:
    print("\nSetup wizard needs extra packages. Run this first:\n\n  pip install -r setup/requirements.txt\n")
    sys.exit(1)

from setup import live_checks, memory_toml  # noqa: E402
from setup.checks import check_git, check_pip, check_python_version  # noqa: E402
from setup.env_writer import TOKEN_KEY, build_env, read_env, write_env_file  # noqa: E402
from setup.service_steps import chroma_check, redis_ping, redis_start_commands  # noqa: E402
from setup.wizard_files import TEMPLATE as GOAT_TEMPLATE, build_goat_toml, write_goat_toml  # noqa: E402

console = Console()


def _load(name: str) -> dict:
    with open(SETUP_DIR / name, "rb") as f:
        return tomllib.load(f)


def _steps(title: str, lines: list[str]) -> None:
    body = "\n".join(f"[bold]{i}.[/bold] {line}" for i, line in enumerate(lines, 1))
    console.print(Panel(body, title=title, expand=False))


def _ask(question):
    """A questionary answer; Ctrl-C ends the wizard without writing anything."""
    answer = question.ask()
    if answer is None:
        console.print("\n[yellow]Setup cancelled — nothing was written.[/yellow]")
        sys.exit(1)
    return answer


def _until_ok(check, describe_fix: str, retry_label: str = "Retry") -> None:
    """Run ``check`` until it passes; on failure say what is wrong and offer to retry (or stop)."""
    while True:
        result = check()
        if result.ok:
            console.print(f"  [green]✓[/green] {result.message}")
            return
        console.print(f"  [red]✗[/red] {result.message}\n    [yellow]→ {result.fix or describe_fix}[/yellow]")
        if _ask(questionary.select("  What now?", choices=[retry_label, "Stop the setup"])) != retry_label:
            console.print("[yellow]Setup stopped — nothing was written.[/yellow]")
            sys.exit(1)


def _secret_until_ok(prompt: str, current: str, check) -> str:
    """Ask for a key/token and test it live; re-enter until it works (or keep it anyway). Empty = keep ``current``."""
    while True:
        hint = "  (Enter keeps the current one)" if current else ""
        value = _ask(questionary.password(prompt + hint)).strip() or current
        result = check(value)
        if result.ok:
            console.print(f"  [green]✓[/green] {result.message}")
            return value
        console.print(f"  [red]✗[/red] {result.message}\n    [yellow]→ {result.fix}[/yellow]")
        choice = _ask(questionary.select("  What now?", choices=["Re-enter it", "Keep it anyway (test it later)"]))
        if choice != "Re-enter it":
            return value


def _basics() -> None:
    for result in (check_python_version(), check_git(), check_pip()):
        if not result.ok:  # cannot be fixed from inside the wizard
            console.print(f"[red]✗ {result.name}: {result.message}[/red]\n  → {result.fix_hint}")
            sys.exit(1)
    console.print("  [green]✓[/green] Python, git and pip are fine\n")


def _provider(providers: list[dict], current: dict) -> tuple[dict, dict[str, str]]:
    console.print("[bold]Step 1 of 4 — AI provider[/bold]  [dim](the model that thinks for GOAT)[/dim]")
    by_id = {p["id"]: p for p in providers}
    choices = [questionary.Choice(f"{p['name']} — {p['notes']}", value=p["id"]) for p in providers]
    default_id = next((p["id"] for p in providers if current.get("BASE_URL", "").startswith(p["base_url"])),
                      next(p["id"] for p in providers if p.get("recommended")))
    chosen = by_id[_ask(questionary.select("  Which provider?", choices=choices, default=default_id))]
    _steps(f"How to get a {chosen['name']} key", chosen["key_steps"])
    key = _secret_until_ok(f"  {chosen['name']} API key", current.get(chosen["env_key"], ""),
                           lambda k: live_checks.check_api_key(chosen["base_url"], k))
    return chosen, {chosen["env_key"]: key}


def _telegram(current: dict, goat_toml: dict) -> tuple[str, str]:
    console.print("\n[bold]Step 2 of 4 — Telegram[/bold]  [dim](where you talk to GOAT)[/dim]")
    _steps("How to create the bot (about a minute)", [
        "In Telegram, open @BotFather and send /newbot",
        "Give it a name, then a username ending in 'bot'",
        "BotFather answers with a token like 123456:ABC-... — copy it and paste it here"])
    token = _secret_until_ok("  Bot token", current.get(TOKEN_KEY, ""), live_checks.check_telegram_token)
    _steps("Your chat id (so only you can use the bot)", [
        "In Telegram, open @userinfobot and send it any message",
        "It answers with your Id (a number) — paste it here"])
    old = str(goat_toml.get("interface", {}).get("telegram", {}).get("admin_chat_id", ""))
    chat_id = _ask(questionary.text("  Your Telegram chat id", default=old,
                                    validate=lambda v: v.strip().lstrip("-").isdigit() or "Only digits")).strip()
    return token, chat_id


def _services(services: list[dict]) -> tuple[dict[str, str], list[str]]:
    console.print("\n[bold]Step 3 of 4 — Services[/bold]  [dim](GOAT's memory)[/dim]")
    now = memory_toml.read_values()
    _steps("Redis (required) — GOAT's short-term memory. Start it with ONE of these, in another terminal:",
           redis_start_commands())
    values = dict(now)

    def ask_redis():
        values["redis_url"] = _ask(questionary.text("  Redis address", default=values["redis_url"])).strip()
        return redis_ping(values["redis_url"])
    _until_ok(ask_redis, "Start Redis, then retry", retry_label="Retry (you can change the address)")

    console.print("  [dim]ChromaDB (required) is GOAT's long-term memory. It runs inside GOAT: nothing to start, "
                  "only the folders where it keeps its data.[/dim]")

    def ask_chroma():
        values["chroma_path"] = _ask(questionary.text("  Long-term memory folder", default=values["chroma_path"])).strip()
        values["chroma_chunks_path"] = _ask(questionary.text("  Its index folder", default=values["chroma_chunks_path"])).strip()
        return chroma_check(*(ROOT / values[k] for k in ("chroma_path", "chroma_chunks_path")))
    _until_ok(ask_chroma, "Choose folders you can write to", retry_label="Retry (you can change the folders)")

    optional = [s for s in services if not s.get("required", False)]
    picked = _ask(questionary.checkbox("  Optional services you have running", choices=[
        questionary.Choice(f"{s['name']} — {s['description']}", value=s["id"]) for s in optional])) if optional else []
    return {k: v for k, v in values.items() if v != now[k]}, picked


def run(reconfigure: bool = False) -> None:
    console.print(Panel("[bold cyan]GOAT 2.0 — Setup[/bold cyan]\n[dim]A few questions, each one checked as you go. "
                        "Nothing is written until the end.[/dim]", expand=False))
    _basics()
    providers, services = _load("providers.toml")["providers"], _load("services.toml")["services"]
    current = read_env(ROOT / ".env") if reconfigure and (ROOT / ".env").exists() else {}
    goat_path = ROOT / "goat2.toml"
    goat_toml = tomllib.loads(goat_path.read_text()) if reconfigure and goat_path.exists() else {}

    chosen, keys = _provider(providers, current)
    token, chat_id = _telegram(current, goat_toml)
    memory_changes, optional = _services(services)

    console.print("\n[bold]Step 4 of 4 — Writing the configuration[/bold]")
    written = []
    template = (SETUP_DIR / "templates" / ".env.example").read_text()
    previous = read_env(ROOT / ".env") if (ROOT / ".env").exists() else {}
    env_backup = write_env_file(ROOT / ".env", build_env(template, token, keys, chosen))
    written.append((".env", "Telegram token, API key, BASE_URL / MODEL_NAME (readable by you only)", env_backup))
    # .env is rebuilt from the template: keys of other providers are not carried over (they stay in the backup)
    dropped = sorted(k for k in previous if k.endswith("_API_KEY") and k.startswith("GOAT_") and k not in keys)
    if memory_changes:
        backup = memory_toml.write_values(memory_changes)
        written.append(("config/memory.toml", ", ".join(f"{k} = {v}" for k, v in memory_changes.items()), backup))
    goat_text = build_goat_toml(GOAT_TEMPLATE.read_text(), chosen["id"], [chosen["id"]], providers, optional,
                                services, chat_id)
    written.append(("goat2.toml", f"provider {chosen['id']}, admin chat {chat_id}", write_goat_toml(ROOT, goat_text)))

    lines = [f"[green]✓[/green] [cyan]{name}[/cyan] — {what}" + (f"  [dim](previous kept as {b.name})[/dim]" if b else "")
             for name, what, b in written]
    if dropped:
        lines.append(f"  [yellow]![/yellow] not kept in .env (only in .env.backup): {', '.join(dropped)}")
    lines += ["", f"Provider [bold]{chosen['name']}[/bold], model [bold]{chosen['fast_model']}[/bold] "
              f"(planner/coder: {chosen['strong_model']})", "", "Next: [bold]./run.sh[/bold]  "
              "(Windows: run.bat) — then send your bot a message on Telegram."]
    console.print(Panel("\n".join(lines), title="Done", expand=False))


if __name__ == "__main__":
    reconfigure = "--reconfigure" in sys.argv
    if (ROOT / "goat2.toml").exists() and (ROOT / ".env").exists() and not reconfigure:
        console.print("[yellow]Setup already complete.[/yellow] Run with --reconfigure to redo it.")
        sys.exit(0)
    run(reconfigure=reconfigure)
