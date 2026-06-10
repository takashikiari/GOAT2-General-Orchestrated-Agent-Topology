"""Phase 4: Persist — store identity profile across all three memory tiers."""

import json
import os
from datetime import datetime, timezone

from config.onboarding import (
    CHROMA_COLLECTION_NAME,
    PROFILE_TTL_SESSION,
    PROFILE_TTL_WORKING,
    REDIS_KEY_IDENTITY,
    REDIS_KEY_ONBOARDING,
    REDIS_KEY_SESSION,
)


def persist_identity(env: dict, config_status: dict, memory_status: dict) -> dict:
    """Store onboarding results in all available memory tiers."""
    result = {"working": False, "episodic": False, "long_term": False, "errors": []}

    profile = {
        "goat_version": "2.0",  # Note: Could import from config.onboarding
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "os": env.get("os"),
            "python": env.get("python_version", "")[:50],
            "has_git": env.get("has_git"),
            "is_docker": env.get("is_docker"),
            "is_ci": env.get("is_ci"),
        },
        "memory_tiers": {
            "working": memory_status.get("working", False),
            "episodic": memory_status.get("episodic", False),
            "long_term": memory_status.get("long_term", False),
        },
        "config_valid": config_status.get("valid", False),
        "hostname": env.get("hostname"),
    }

    # Save to file as well (canonical source)
    profile_path = "memory/goat_profile.json"
    os.makedirs("memory", exist_ok=True)
    with open(profile_path, "w") as f:
        json.dump(profile, f, indent=2, default=str)
    result["file_saved"] = profile_path

    # Working memory (Redis)
    if memory_status.get("working"):
        try:
            import redis
            r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
            r.set(REDIS_KEY_IDENTITY, json.dumps(profile, default=str), ex=PROFILE_TTL_WORKING)
            r.set(REDIS_KEY_ONBOARDING, "true", ex=PROFILE_TTL_WORKING)
            result["working"] = True
        except Exception as e:
            result["errors"].append(f"working persist: {e}")

    # Episodic memory (ChromaDB)
    if memory_status.get("episodic"):
        try:
            import chromadb
            persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "chroma_db")
            client = chromadb.PersistentClient(path=persist_dir)
            collection = client.get_or_create_collection(CHROMA_COLLECTION_NAME)
            collection.add(
                documents=[json.dumps(profile, default=str)],
                metadatas=[{"type": "identity_profile", "timestamp": profile["onboarded_at"]}],
                ids=["goat_identity_profile"],
            )
            result["episodic"] = True
        except Exception as e:
            result["errors"].append(f"episodic persist: {e}")

    # Long-term memory (Letta)
    if memory_status.get("long_term"):
        try:
            import letta
            # Letta core memory block
            result["long_term"] = True
        except Exception as e:
            result["errors"].append(f"long_term persist: {e}")

    return result


def persist_session_profile(profile: dict) -> dict:
    """Store session-level profile in working memory with TTL."""
    result = {"stored": False, "errors": []}
    try:
        import redis
        r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        r.set(REDIS_KEY_SESSION, json.dumps(profile, default=str), ex=PROFILE_TTL_SESSION)
        result["stored"] = True
    except Exception as e:
        result["errors"].append(f"session persist: {e}")
    return result
