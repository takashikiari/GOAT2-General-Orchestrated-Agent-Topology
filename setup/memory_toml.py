"""setup.memory_toml — the wizard writes the service addresses where GOAT reads them: config/memory.toml.

Only three values, each replaced in place inside its section so every comment and every other setting stays as it was:
[working] storage_url (Redis), [episodic] storage_path and chunk_storage_path (ChromaDB's local data folders). The file
is copied to memory.toml.backup first. No third-party imports.
"""
from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

__all__ = ["MEMORY_TOML", "read_values", "write_values", "KEYS"]

MEMORY_TOML = Path(__file__).resolve().parent.parent / "config" / "memory.toml"
KEYS = {"redis_url": ("working", "storage_url"), "chroma_path": ("episodic", "storage_path"),
        "chroma_chunks_path": ("episodic", "chunk_storage_path")}


def read_values(path: Path | None = None) -> dict[str, str]:
    """The current values (as GOAT will read them), for the wizard's defaults."""
    with open(path or MEMORY_TOML, "rb") as f:
        cfg = tomllib.load(f)
    return {name: str(cfg.get(section, {}).get(key, "")) for name, (section, key) in KEYS.items()}


def _replace(text: str, section: str, key: str, value: str) -> str:
    out, in_section, done = [], False, False
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)(.*)$")
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped.split("#")[0].strip() == f"[{section}]"
        m = pattern.match(line.rstrip("\n")) if in_section and not done else None
        if m:
            out.append(f'{m.group(1)}{key} = "{value}"{m.group(3)}\n')
            done = True
            continue
        out.append(line)
    if not done:
        raise KeyError(f"[{section}] {key} not found in memory.toml")
    return "".join(out)


def write_values(values: dict[str, str], path: Path | None = None) -> Path:
    """Write the given values (keys of KEYS) in place; returns the backup's path. Raises KeyError for a missing key."""
    path = Path(path or MEMORY_TOML)
    text = path.read_text(encoding="utf-8")
    for name, value in values.items():
        section, key = KEYS[name]
        text = _replace(text, section, key, value)
    tomllib.loads(text)  # never leave GOAT a file it cannot read
    backup = path.with_name(path.name + ".backup")
    shutil.copyfile(path, backup)
    path.write_text(text, encoding="utf-8")
    return backup
