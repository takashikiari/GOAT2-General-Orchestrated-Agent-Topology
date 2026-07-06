"""setup.checks — pre-flight system checks.

Run automatically by the wizard and run.sh before starting GOAT.
Each check returns a CheckResult; required failures abort the process,
optional failures show a warning and continue.

Usage (standalone):
    python3 setup/checks.py
"""
from __future__ import annotations

import shutil
import socket
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    required: bool = True
    fix_hint: str = ""


def check_python_version(min_version: tuple[int, int] = (3, 11)) -> CheckResult:
    current = (sys.version_info.major, sys.version_info.minor)
    ok = current >= min_version
    return CheckResult(
        name="Python version",
        ok=ok,
        message=f"Python {current[0]}.{current[1]}",
        required=True,
        fix_hint=f"Install Python {min_version[0]}.{min_version[1]}+ from https://python.org",
    )


def check_git() -> CheckResult:
    ok = shutil.which("git") is not None
    return CheckResult(
        name="Git",
        ok=ok,
        message="git found" if ok else "git not found",
        required=True,
        fix_hint="Install git from https://git-scm.com",
    )


def check_pip() -> CheckResult:
    ok = shutil.which("pip3") is not None or shutil.which("pip") is not None
    return CheckResult(
        name="pip",
        ok=ok,
        message="pip found" if ok else "pip not found",
        required=True,
        fix_hint="Install pip: python3 -m ensurepip --upgrade",
    )


def check_redis(url: str = "redis://localhost:6379/0") -> CheckResult:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        return CheckResult(
            name="Redis",
            ok=True,
            message=f"reachable at {host}:{port}",
            required=False,
        )
    except OSError:
        return CheckResult(
            name="Redis",
            ok=False,
            message=f"not reachable at {host}:{port}",
            required=False,
            fix_hint="Install Redis: https://redis.io/docs/getting-started/ — or set services.redis.enabled = false in goat2.toml",
        )


def check_chroma() -> CheckResult:
    try:
        import importlib.util
        ok = importlib.util.find_spec("chromadb") is not None
        return CheckResult(
            name="ChromaDB",
            ok=ok,
            message="chromadb installed" if ok else "chromadb not installed",
            required=False,
            fix_hint="pip install chromadb — or set services.chroma.enabled = false in goat2.toml",
        )
    except Exception:
        return CheckResult("ChromaDB", False, "chromadb check failed", required=False)


def check_disk_space(min_mb: int = 500) -> CheckResult:
    import shutil as _shutil
    try:
        total, used, free = _shutil.disk_usage(".")
        free_mb = free // (1024 * 1024)
        ok = free_mb >= min_mb
        return CheckResult(
            name="Disk space",
            ok=ok,
            message=f"{free_mb} MB free",
            required=False,
            fix_hint=f"Free up at least {min_mb} MB of disk space",
        )
    except Exception:
        return CheckResult("Disk space", True, "check skipped", required=False)


def run_all(redis_url: str = "redis://localhost:6379/0") -> list[CheckResult]:
    """Run all checks and return results."""
    return [
        check_python_version(),
        check_git(),
        check_pip(),
        check_redis(redis_url),
        check_chroma(),
        check_disk_space(),
    ]


def print_results(results: list[CheckResult]) -> bool:
    """Print results with color. Returns True if all required checks passed."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(show_header=False, box=None, padding=(0, 1))
        for r in results:
            icon = "✅" if r.ok else ("❌" if r.required else "⚠️ ")
            label = f"[bold]{r.name}[/bold]"
            status = f"[green]{r.message}[/green]" if r.ok else (
                f"[red]{r.message}[/red]" if r.required else f"[yellow]{r.message}[/yellow]"
            )
            table.add_row(icon, label, status)
            if not r.ok and r.fix_hint:
                table.add_row("  ", "", f"[dim]→ {r.fix_hint}[/dim]")
        console.print(table)
    except ImportError:
        for r in results:
            icon = "OK" if r.ok else ("FAIL" if r.required else "WARN")
            print(f"  [{icon}] {r.name}: {r.message}")
            if not r.ok and r.fix_hint:
                print(f"        → {r.fix_hint}")

    return all(r.ok for r in results if r.required)


if __name__ == "__main__":
    results = run_all()
    ok = print_results(results)
    sys.exit(0 if ok else 1)
