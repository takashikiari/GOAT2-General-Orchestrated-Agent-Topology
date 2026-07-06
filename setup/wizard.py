"""setup.wizard — interactive first-run configuration wizard.

Guides a non-developer through selecting providers, entering API keys,
configuring optional services, and generating goat2.toml + .env.

Usage:
    python3 setup/wizard.py
    python3 setup/wizard.py --reconfigure   # run again on existing setup
"""
from __future__ import annotations

import os
import re
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP_DIR = Path(__file__).parent

# ── dependency guard ──────────────────────────────────────────────────────────
_MISSING = []
try:
    import questionary
except ImportError:
    _MISSING.append("questionary")
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    _MISSING.append("rich")

if _MISSING:
    print(f"\nSetup wizard needs extra packages. Run this first:\n")
    print(f"  pip install -r setup/requirements.txt\n")
    sys.exit(1)

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        print("Python 3.11+ required. Please upgrade Python.")
        sys.exit(1)

console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _write_toml_value(lines: list[str], key: str, value: str) -> list[str]:
    """Replace a key = value line in a TOML lines list."""
    pattern = re.compile(rf'^{re.escape(key)}\s*=')
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            lines[i] = f'{key} = "{value}"\n'
            return lines
    return lines


def _section_replace(content: str, section: str, key: str, value: str) -> str:
    """Replace key under [section] in TOML content string."""
    in_section = False
    result = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == f"[{section}]"
        if in_section and re.match(rf'^{re.escape(key)}\s*=', stripped):
            indent = len(line) - len(line.lstrip())
            result.append(" " * indent + f'{key} = "{value}"\n')
            continue
        result.append(line)
    return "".join(result)


# ── main wizard ───────────────────────────────────────────────────────────────

def run(reconfigure: bool = False) -> None:
    console.print(Panel(
        Text.from_markup(
            "[bold cyan]GOAT 2.0 — Setup Wizard[/bold cyan]\n"
            "[dim]Answer a few questions and you'll be ready to go.[/dim]"
        ),
        expand=False,
    ))
    console.print()

    # ── pre-flight checks ─────────────────────────────────────────────────────
    console.print("[bold]Checking your system...[/bold]")
    from setup.checks import run_all, print_results
    results = run_all()
    ok = print_results(results)
    console.print()
    if not ok:
        console.print("[red]Some required checks failed. Fix the issues above and run the wizard again.[/red]")
        sys.exit(1)

    # ── load provider list ────────────────────────────────────────────────────
    providers_data = _load_toml(SETUP_DIR / "providers.toml")
    services_data  = _load_toml(SETUP_DIR / "services.toml")
    all_providers  = providers_data["providers"]
    all_services   = services_data["services"]

    # ── step 1: select providers ──────────────────────────────────────────────
    console.print("[bold]Step 1 of 4 — LLM Providers[/bold]")
    console.print("[dim]Select which AI providers you want to use.[/dim]\n")

    provider_choices = [
        questionary.Choice(
            title=f"{p['name']}  [dim]{p['notes']}[/dim]",
            value=p["id"],
            checked=p.get("recommended", False),
        )
        for p in all_providers
    ]

    selected_provider_ids: list[str] = questionary.checkbox(
        "Which providers?  (Space to select, Enter to confirm)",
        choices=provider_choices,
        instruction="(at least one required)",
    ).ask()

    if not selected_provider_ids:
        console.print("[red]At least one provider is required.[/red]")
        sys.exit(1)

    # ── step 2: API keys ──────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Step 2 of 4 — API Keys[/bold]")
    console.print("[dim]Enter your API key for each selected provider. Keys are saved to .env[/dim]\n")

    provider_map = {p["id"]: p for p in all_providers}
    api_keys: dict[str, str] = {}
    default_provider = selected_provider_ids[0]

    for pid in selected_provider_ids:
        p = provider_map[pid]
        if not p.get("env_key"):
            console.print(f"  [green]✓[/green] {p['name']} — no API key needed")
            continue
        key = questionary.password(
            f"  {p['name']} API key  ({p['docs_url']})",
        ).ask()
        if key and key.strip():
            api_keys[p["env_key"]] = key.strip()
        else:
            console.print(f"  [yellow]⚠ Skipped {p['name']} — you can add the key to .env later[/yellow]")

    if len(selected_provider_ids) > 1:
        console.print()
        default_provider = questionary.select(
            "  Which provider should be the default?",
            choices=selected_provider_ids,
        ).ask()

    # ── step 3: services ──────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Step 3 of 4 — Services[/bold]")
    console.print("[dim]Optional services extend GOAT's memory and functionality.[/dim]\n")

    non_required_services = [s for s in all_services if not s.get("required", False)]
    service_choices = [
        questionary.Choice(
            title=f"{s['name']}  [dim]{s['description']}[/dim]",
            value=s["id"],
            checked=s.get("recommended", False),
        )
        for s in non_required_services
    ]

    selected_service_ids: list[str] = questionary.checkbox(
        "Which optional services do you have running?  (uncheck if not installed)",
        choices=service_choices,
    ).ask() or []

    # ── step 4: telegram ──────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Step 4 of 4 — Telegram[/bold]")
    console.print("[dim]GOAT communicates through a Telegram bot.[/dim]\n")
    console.print("  [dim]Create a bot at[/dim] [link=https://t.me/BotFather]t.me/BotFather[/link] [dim]— takes ~1 minute.[/dim]")
    console.print()

    telegram_token = questionary.password(
        "  Telegram bot token  (from @BotFather)",
    ).ask() or ""

    console.print()
    console.print("  [dim]Find your chat ID by messaging[/dim] [link=https://t.me/userinfobot]@userinfobot[/link] [dim]on Telegram.[/dim]")
    admin_chat_id = questionary.text(
        "  Your Telegram chat ID  (for admin notifications, optional)",
        default="",
    ).ask() or ""

    # ── generate files ────────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Generating configuration files...[/bold]")

    _write_env(telegram_token, api_keys)
    _write_goat_toml(
        default_provider=default_provider,
        selected_providers=selected_provider_ids,
        all_providers=all_providers,
        selected_services=selected_service_ids,
        all_services=all_services,
        admin_chat_id=admin_chat_id,
    )

    console.print()
    console.print(Panel(
        Text.from_markup(
            "[bold green]Setup complete![/bold green]\n\n"
            "  [dim]Your config:[/dim]  [cyan]goat2.toml[/cyan]\n"
            "  [dim]Your keys:[/dim]   [cyan].env[/cyan]  [dim](never commit this file)[/dim]\n\n"
            "  Run [bold cyan]./run.sh[/bold cyan] to start GOAT."
        ),
        expand=False,
    ))


# ── file writers ──────────────────────────────────────────────────────────────

def _write_env(telegram_token: str, api_keys: dict[str, str]) -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        backup = ROOT / ".env.backup"
        shutil.copy(env_path, backup)
        console.print(f"  [dim]Backed up existing .env → .env.backup[/dim]")

    template = (SETUP_DIR / "templates" / ".env.example").read_text()
    lines = template.splitlines(keepends=True)

    def _set(lines: list[str], key: str, value: str) -> list[str]:
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                return lines
        lines.append(f"{key}={value}\n")
        return lines

    if telegram_token:
        lines = _set(lines, "TELEGRAM_TOKEN", telegram_token)
    for env_key, value in api_keys.items():
        lines = _set(lines, env_key, value)

    env_path.write_text("".join(lines))
    console.print(f"  [green]✓[/green] .env written")


def _write_goat_toml(
    default_provider: str,
    selected_providers: list[str],
    all_providers: list[dict],
    selected_services: list[str],
    all_services: list[dict],
    admin_chat_id: str,
) -> None:
    toml_path = ROOT / "goat2.toml"
    if toml_path.exists():
        backup = ROOT / "goat2.toml.backup"
        shutil.copy(toml_path, backup)
        console.print(f"  [dim]Backed up existing goat2.toml → goat2.toml.backup[/dim]")

    template = (SETUP_DIR / "templates" / "goat2.default.toml").read_text()

    # Set default provider
    template = _section_replace(template, "providers", "default", default_provider)

    # Enable/disable providers
    provider_map = {p["id"]: p for p in all_providers}
    for pid, p in provider_map.items():
        enabled = "true" if pid in selected_providers else "false"
        section = f"providers.{pid}"
        # Replace enabled = ... under the right section
        in_section = False
        lines = template.splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = stripped == f"[{section}]"
            if in_section and re.match(r'^enabled\s*=', stripped):
                lines[i] = f"enabled = {enabled}\n"
                break
        template = "".join(lines)

    # Enable/disable services
    service_map = {s["id"]: s for s in all_services}
    for sid in service_map:
        if sid == "telegram":
            continue
        enabled = "true" if sid in selected_services else "false"
        section = f"services.{sid}"
        in_section = False
        lines = template.splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = stripped == f"[{section}]"
            if in_section and re.match(r'^enabled\s*=', stripped):
                lines[i] = f"enabled = {enabled}\n"
                break
        template = "".join(lines)

    # Set admin_chat_id
    if admin_chat_id:
        template = _section_replace(template, "interface.telegram", "admin_chat_id", admin_chat_id)

    toml_path.write_text(template)
    console.print(f"  [green]✓[/green] goat2.toml written")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    reconfigure = "--reconfigure" in sys.argv
    existing = (ROOT / "goat2.toml").exists() and (ROOT / ".env").exists()

    if existing and not reconfigure:
        console.print("[yellow]Setup already complete.[/yellow] Run with --reconfigure to redo it.")
        sys.exit(0)

    run(reconfigure=reconfigure)
