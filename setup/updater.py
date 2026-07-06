"""setup.updater — version check and update installer.

Checks GitHub Releases for a newer version, shows the changelog,
asks for confirmation, then runs git pull + pip install + restart.

Usage (CLI):
    python3 setup/updater.py --check        # check only, no install
    python3 setup/updater.py --install      # install latest without asking
    python3 setup/updater.py                # interactive (check + ask)

Called by:
    - run.sh on startup (check only)
    - Telegram /update command (interactive)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _current_version() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from version import __version__
        return __version__
    except ImportError:
        return "0.0.0"


def _load_config() -> dict:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    cfg_path = ROOT / "goat2.toml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "rb") as f:
        return tomllib.load(f)


def _github_repo(config: dict) -> str:
    return config.get("updates", {}).get("github_repo", "")


def _channel(config: dict) -> str:
    return config.get("updates", {}).get("channel", "stable")


def check_for_update(repo: str, channel: str = "stable") -> dict | None:
    """Query GitHub Releases API. Returns release dict if newer, else None."""
    if not repo:
        return None
    try:
        import requests
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(url, timeout=8, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return None
        release = resp.json()

        # If beta channel, also check pre-releases via /releases endpoint
        if channel == "beta":
            all_resp = requests.get(
                f"https://api.github.com/repos/{repo}/releases",
                timeout=8,
                headers={"Accept": "application/vnd.github+json"},
            )
            if all_resp.status_code == 200:
                all_releases = all_resp.json()
                if all_releases:
                    release = all_releases[0]

        remote_tag = release.get("tag_name", "v0.0.0").lstrip("v")
        current    = _current_version()

        if _version_newer(remote_tag, current):
            return release
        return None
    except Exception:
        return None


def _version_newer(remote: str, current: str) -> bool:
    """Simple semver comparison."""
    def _parts(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0, 0, 0)
    return _parts(remote) > _parts(current)


def install_update(confirm: bool = True) -> bool:
    """Pull latest code, reinstall deps, restart process.

    Returns True if update was applied, False if skipped/failed.
    """
    config  = _load_config()
    repo    = _github_repo(config)
    channel = _channel(config)

    release = check_for_update(repo, channel)
    current = _current_version()

    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        _rich = True
    except ImportError:
        _rich = False
        console = None  # type: ignore[assignment]

    if release is None:
        msg = f"GOAT is up to date (v{current})."
        if _rich:
            console.print(f"[green]✓[/green] {msg}")
        else:
            print(f"✓ {msg}")
        return False

    remote_tag  = release.get("tag_name", "?")
    changelog   = release.get("body", "No changelog provided.").strip()
    release_url = release.get("html_url", "")

    if _rich:
        console.print(Panel(
            f"[bold cyan]New version available: {remote_tag}[/bold cyan]  (current: v{current})\n\n"
            f"[bold]What's new:[/bold]\n{changelog}\n\n"
            f"[dim]{release_url}[/dim]",
            title="Update available",
            expand=False,
        ))
    else:
        print(f"\nNew version: {remote_tag} (current: v{current})")
        print(f"What's new:\n{changelog}\n")

    if confirm:
        try:
            import questionary
            ok = questionary.confirm("Install update now?", default=True).ask()
        except ImportError:
            ok = input("Install update? [Y/n] ").strip().lower() in ("", "y", "yes")
        if not ok:
            print("Update skipped.")
            return False

    # ── apply update ──────────────────────────────────────────────────────────
    print("\nApplying update...")

    git = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, text=True)
    if git.returncode != 0:
        print(f"git pull failed:\n{git.stderr}")
        return False
    print("  ✓ Code updated")

    pip_cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")]
    pip = subprocess.run(pip_cmd, cwd=ROOT, capture_output=True, text=True)
    if pip.returncode != 0:
        print(f"pip install failed:\n{pip.stderr}")
        return False
    print("  ✓ Dependencies updated")

    print("\nRestarting GOAT...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
    return True  # unreachable; kept for type checker


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--check" in args:
        config  = _load_config()
        repo    = _github_repo(config)
        channel = _channel(config)
        release = check_for_update(repo, channel)
        if release:
            print(f"Update available: {release.get('tag_name')}")
            sys.exit(0)
        else:
            print(f"Up to date (v{_current_version()})")
            sys.exit(0)

    confirm = "--install" not in args
    install_update(confirm=confirm)
