"""Phase 1: Detect environment — OS, Python, deps, tools, config."""

import os
import sys
import subprocess
import platform
from pathlib import Path

from config.onboarding import CONFIG_FILE_NAME, ENV_FILE_NAME


def detect_environment() -> dict:
    """Detect and return full environment snapshot."""
    env = {
        "os": platform.system(),
        "os_version": platform.version(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
        "hostname": platform.node(),
        "is_docker": _is_docker(),
        "is_ci": _is_ci(),
        "has_git": _has_git(),
        "has_redis": _has_redis(),
        "has_chromadb": _has_chromadb(),
        "has_letta": _has_letta(),
        "has_searxng": _has_searxng(),
        "env_file_exists": Path(ENV_FILE_NAME).exists(),
        "goat_config_exists": Path(CONFIG_FILE_NAME).exists(),
        "goat_config_valid": False,  # filled later
    }
    return env


def _is_docker() -> bool:
    return Path("/.dockerenv").exists()


def _is_ci() -> bool:
    return any(os.environ.get(var) for var in ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"])


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _has_redis() -> bool:
    try:
        import redis
        r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        r.ping()
        return True
    except Exception:
        return False


def _has_chromadb() -> bool:
    try:
        import chromadb
        return True
    except ImportError:
        return False


def _has_letta() -> bool:
    try:
        import letta
        return True
    except ImportError:
        return False


def _has_searxng() -> bool:
    url = os.environ.get("SEARXNG_URL", "http://localhost:7777")
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=3)
        return True
    except Exception:
        return False
