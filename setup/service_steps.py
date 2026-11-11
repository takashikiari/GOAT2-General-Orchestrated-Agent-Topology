"""setup.service_steps — what the wizard's "Services" step checks, with no third-party imports.

Redis (required) is a server: the wizard shows how to start it on this machine, tests it with a real PING at the
address the user gives, and retries until it answers. ChromaDB (required) is NOT a server in GOAT: it is embedded
(chromadb.PersistentClient on a local folder, memory/episodic/episodic.py), so its step only checks the package is
installed and that the data folders can be created and written. Letta stays optional.
"""
from __future__ import annotations

import importlib.util
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

__all__ = ["Outcome", "redis_start_commands", "redis_ping", "chroma_check", "DEFAULT_REDIS_URL"]

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str  # what was found, in plain words
    fix: str = ""  # what to do about it when not ok


def redis_start_commands(which: Callable[[str], str | None] = shutil.which, platform: str | None = None) -> list[str]:
    """Copyable commands that start Redis on this machine, the ones whose tool is installed first."""
    platform = platform or sys.platform
    docker = "docker run -d --name goat-redis -p 6379:6379 --restart unless-stopped redis:7"
    found, other = [], []
    (found if which("docker") else other).append(docker)
    if platform == "darwin":
        (found if which("brew") else other).append("brew install redis && brew services start redis")
    elif platform.startswith("linux"):
        (found if which("apt-get") else other).append("sudo apt-get install -y redis-server && sudo systemctl enable --now redis-server")
        if which("dnf"):
            found.append("sudo dnf install -y redis && sudo systemctl enable --now redis")
    else:
        other.append("Windows: install Docker Desktop, then the docker command above (or use WSL)")
    return found + other


def redis_ping(url: str, timeout: float = 2.0) -> Outcome:
    """A real Redis PING at ``url`` (raw protocol, so it works before GOAT's packages are importable)."""
    parsed = urlparse(url or DEFAULT_REDIS_URL)
    if parsed.scheme not in ("redis", "rediss"):
        return Outcome(False, f"'{url}' is not a Redis address", "It should look like redis://localhost:6379/0")
    host, port = parsed.hostname or "localhost", parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            if parsed.password:
                user = parsed.username or "default"
                s.sendall(_command("AUTH", user, parsed.password))
                if not s.recv(64).startswith(b"+OK"):
                    return Outcome(False, f"Redis at {host}:{port} refused the password", "Check the password in the address")
            s.sendall(_command("PING"))
            reply = s.recv(64)
    except OSError:
        return Outcome(False, f"nothing answers at {host}:{port}", "Start Redis with one of the commands above, then retry")
    if reply.startswith(b"+PONG"):
        return Outcome(True, f"Redis answers at {host}:{port}")
    return Outcome(False, f"{host}:{port} answered, but not like Redis ({reply[:30]!r})", "Is it really Redis on that port?")


def _command(*parts: str) -> bytes:
    out = f"*{len(parts)}\r\n"
    for p in parts:
        out += f"${len(p.encode())}\r\n{p}\r\n"
    return out.encode()


def chroma_check(*folders: Path, find_spec: Callable = importlib.util.find_spec) -> Outcome:
    """chromadb installed, and every data folder can be created and written."""
    if find_spec("chromadb") is None:
        return Outcome(False, "the chromadb package is not installed", "Run: pip install -r requirements.txt")
    for folder in folders:
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".goat-write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            return Outcome(False, f"cannot write to {folder} ({exc.strerror or type(exc).__name__})",
                           "Choose a folder you can write to, or fix its permissions")
    return Outcome(True, "chromadb installed; data folders writable: " + ", ".join(str(f) for f in folders))
