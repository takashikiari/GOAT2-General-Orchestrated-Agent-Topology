"""Phase 2: Configure — install deps, validate config, set up memory tiers."""

import os
import subprocess
import sys
from pathlib import Path

from config.onboarding import (
    CONFIG_FILE_NAME,
    CONFIG_REQUIRED_SECTIONS,
    ENV_FILE_NAME,
    PIP_TIMEOUT_SECONDS,
)


def install_requirements(env: dict) -> dict:
    """Install production requirements based on environment."""
    result = {"pip_installed": False, "extras_installed": [], "errors": []}

    # Core requirements
    req_files = [
        ("requirements.txt", "core"),
        ("requirements-minimal.txt", "minimal"),
    ]

    for req_file, label in req_files:
        if Path(req_file).exists():
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"],
                    capture_output=True, check=True, timeout=PIP_TIMEOUT_SECONDS
                )
                result["extras_installed"].append(label)
            except subprocess.CalledProcessError as e:
                result["errors"].append(f"{label}: {e.stderr.decode()[:200]}")
        else:
            result["errors"].append(f"{label}: {req_file} not found")

    result["pip_installed"] = len(result["errors"]) == 0
    return result


def validate_goat_config() -> dict:
    """Validate goat.toml and return config status."""
    result = {"valid": False, "errors": [], "sections": {}}

    config_path = Path(CONFIG_FILE_NAME)
    if not config_path.exists():
        result["errors"].append(f"{CONFIG_FILE_NAME} not found")
        return result

    try:
        import tomllib
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except ImportError:
        try:
            import tomli as tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
        except ImportError:
            result["errors"].append("tomllib/tomli not available")
            return result
    except Exception as e:
        result["errors"].append(f"parse error: {e}")
        return result

    for section in CONFIG_REQUIRED_SECTIONS:
        if section in config:
            result["sections"][section] = list(config[section].keys())
        else:
            result["errors"].append(f"missing section: [{section}]")

    # Validate API keys
    api_keys = config.get("api_keys", {})
    has_env_keys = any(os.environ.get(k.upper()) for k in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY"])
    has_config_keys = any(v for v in api_keys.values())
    if not has_env_keys and not has_config_keys:
        result["errors"].append("no API keys found (env or config)")

    result["valid"] = len(result["errors"]) == 0
    return result


def setup_memory_tiers(env: dict) -> dict:
    """Initialize and verify all three memory tiers."""
    result = {"working": False, "episodic": False, "long_term": False, "errors": []}

    # Working memory (Redis)
    if env.get("has_redis"):
        try:
            import redis
            r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
            r.set("goat:onboarding:ping", "1", ex=10)
            r.delete("goat:onboarding:ping")
            result["working"] = True
        except Exception as e:
            result["errors"].append(f"working (Redis): {e}")
    else:
        result["errors"].append("working (Redis): not available")

    # Episodic memory (ChromaDB)
    if env.get("has_chromadb"):
        try:
            import chromadb
            persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "chroma_db")
            client = chromadb.PersistentClient(path=persist_dir)
            client.heartbeat()
            result["episodic"] = True
        except Exception as e:
            result["errors"].append(f"episodic (ChromaDB): {e}")
    else:
        result["errors"].append("episodic (ChromaDB): not available")

    # Long-term memory (Letta)
    if env.get("has_letta"):
        try:
            import letta
            result["long_term"] = True
        except Exception as e:
            result["errors"].append(f"long_term (Letta): {e}")
    else:
        result["errors"].append("long_term (Letta): not available")

    return result
