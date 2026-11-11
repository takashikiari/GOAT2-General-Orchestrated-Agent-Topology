"""setup.wizard_files — goat2.toml as the wizard writes it (no third-party imports, so it is testable on its own).

goat2.toml is GOAT's own record of the setup: the default provider, which providers and optional services were chosen,
the admin chat id (read by config/admin_chat.py). Required services (Redis, ChromaDB) are always enabled there. The
previous file is kept as goat2.toml.backup.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

__all__ = ["build_goat_toml", "write_goat_toml", "TEMPLATE"]

TEMPLATE = Path(__file__).resolve().parent / "templates" / "goat2.default.toml"


def _set_in_section(content: str, section: str, key: str, value: str) -> str:
    """``key = value`` (value already TOML-formatted) under ``[section]``; other lines untouched."""
    in_section, out = False, []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == f"[{section}]"
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped):
            indent = len(line) - len(line.lstrip())
            out.append(" " * indent + f"{key} = {value}\n")
            in_section = False
            continue
        out.append(line)
    return "".join(out)


def build_goat_toml(template: str, default_provider: str, providers: list[str], all_providers: list[dict],
                    optional_services: list[str], all_services: list[dict], admin_chat_id: str) -> str:
    text = _set_in_section(template, "providers", "default", f'"{default_provider}"')
    for p in all_providers:
        text = _set_in_section(text, f"providers.{p['id']}", "enabled", "true" if p["id"] in providers else "false")
    for s in all_services:
        if s["id"] == "telegram":
            continue
        enabled = s.get("required", False) or s["id"] in optional_services  # required ones always on
        text = _set_in_section(text, f"services.{s['id']}", "enabled", "true" if enabled else "false")
    if admin_chat_id:
        text = _set_in_section(text, "interface.telegram", "admin_chat_id", f'"{admin_chat_id}"')
    return text


def write_goat_toml(root: Path, text: str) -> Path | None:
    """Write goat2.toml under ``root``; returns the backup's path when there was a previous one."""
    path = Path(root) / "goat2.toml"
    backup = None
    if path.exists():
        backup = path.with_name("goat2.toml.backup")
        shutil.copyfile(path, backup)
    path.write_text(text)
    return backup
