"""setup.rollback — revert to a previous release tag.

Lists available git tags, lets the user pick one, checks out that tag,
and reinstalls dependencies for that version.

Usage:
    python3 setup/rollback.py                   # interactive picker
    python3 setup/rollback.py --to v0.2.1       # non-interactive
    python3 setup/rollback.py --list            # print available versions
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def list_versions() -> list[str]:
    """Return all release tags sorted newest-first."""
    result = _git("tag", "--sort=-version:refname")
    if result.returncode != 0:
        return []
    return [t for t in result.stdout.strip().splitlines() if t.startswith("v")]


def _current_version() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from version import __version__
        return f"v{__version__}"
    except ImportError:
        return "unknown"


def rollback(tag: str, confirm: bool = True) -> bool:
    """Checkout *tag* and reinstall requirements. Returns True on success."""
    try:
        from rich.console import Console
        console = Console()
        _rich = True
    except ImportError:
        _rich = False
        console = None  # type: ignore[assignment]

    current = _current_version()
    msg = f"Rolling back from [bold]{current}[/bold] to [bold cyan]{tag}[/bold cyan]"
    if _rich:
        console.print(f"\n{msg}\n")
    else:
        print(f"\nRolling back from {current} to {tag}\n")

    if confirm:
        try:
            import questionary
            ok = questionary.confirm(f"Confirm rollback to {tag}?", default=False).ask()
        except ImportError:
            ok = input(f"Confirm rollback to {tag}? [y/N] ").strip().lower() in ("y", "yes")
        if not ok:
            print("Rollback cancelled.")
            return False

    # Stash any local changes so checkout doesn't fail
    _git("stash", "--include-untracked")

    checkout = _git("checkout", tag)
    if checkout.returncode != 0:
        print(f"Failed to checkout {tag}:\n{checkout.stderr}")
        return False
    print(f"  ✓ Checked out {tag}")

    pip_cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")]
    pip = subprocess.run(pip_cmd, cwd=ROOT, capture_output=True, text=True)
    if pip.returncode != 0:
        print(f"pip install failed:\n{pip.stderr}")
        return False
    print("  ✓ Dependencies reinstalled")

    print(f"\nRollback to {tag} complete. Restart GOAT to apply.\n")
    return True


def interactive() -> None:
    versions = list_versions()
    if not versions:
        print("No release tags found. Make sure you have git history.")
        sys.exit(1)

    current = _current_version()

    try:
        from rich.console import Console
        Console().print(f"\n[dim]Current version: {current}[/dim]\n")
        import questionary
        tag = questionary.select(
            "Select version to restore:",
            choices=versions,
        ).ask()
    except ImportError:
        print(f"Current version: {current}")
        print("Available versions:")
        for i, v in enumerate(versions, 1):
            print(f"  {i}. {v}")
        choice = input("Enter number: ").strip()
        try:
            tag = versions[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            sys.exit(1)

    if tag:
        rollback(tag)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--list" in args:
        versions = list_versions()
        print(f"Current: {_current_version()}")
        print("Available versions:")
        for v in versions:
            print(f"  {v}")
        sys.exit(0)

    to_idx = next((i for i, a in enumerate(args) if a == "--to"), None)
    if to_idx is not None and to_idx + 1 < len(args):
        tag = args[to_idx + 1]
        confirm = "--yes" not in args
        success = rollback(tag, confirm=confirm)
        sys.exit(0 if success else 1)

    interactive()
