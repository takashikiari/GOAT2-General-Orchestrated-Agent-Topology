"""tests.test_promote — episodic → L1 promotion: cap guard, upsert, resilience.

Exercises ``memory.promote.promote_fact`` with a fake Letta (no network) so the
gating logic — upsert-by-key, L1 token cap, empty-key refusal, Letta-failure
surfacing — is verified. Mirrors the suite's ``asyncio.run`` convention.
"""
from __future__ import annotations

import asyncio

from memory.config import L1_FACTS_MAX_TOKENS
from memory.promote import promote_fact


class _FakePermanent:
    """In-memory Letta core-memory ``facts`` block stand-in (key→value dict)."""

    def __init__(self, facts: dict[str, str] | None = None, *, broken: bool = False) -> None:
        self._facts = dict(facts or {})
        self.broken = broken

    async def get_all_facts(self) -> dict[str, str]:
        if self.broken:
            raise RuntimeError("letta down")
        return dict(self._facts)

    async def store_fact(self, key: str, value: str) -> None:
        self._facts[key] = value


def test_upsert_adds_new_fact():
    p = _FakePermanent()
    out = asyncio.run(promote_fact(p, "user_name", "Takashi"))
    assert out.startswith("✅") and p._facts == {"user_name": "Takashi"}


def test_upsert_updates_existing_key_no_duplicate():
    p = _FakePermanent({"user_name": "Takashi"})
    out = asyncio.run(promote_fact(p, "user_name", "Taka"))
    assert out.startswith("✅")
    assert p._facts == {"user_name": "Taka"}            # updated, not duplicated


def test_empty_key_refused():
    p = _FakePermanent()
    out = asyncio.run(promote_fact(p, "", "x"))
    assert out.startswith("❌") and p._facts == {}      # no write


def test_cap_guard_rejects_when_full():
    # Fill the block to the cap with one giant value, then a new key is refused.
    p = _FakePermanent()
    big = "x" * (L1_FACTS_MAX_TOKENS * 4 - 8)          # one "- blob: <val>" = cap tokens
    assert asyncio.run(promote_fact(p, "blob", big)).startswith("✅")
    out = asyncio.run(promote_fact(p, "second", "y"))
    assert out.startswith("❌") and "full" in out and "second" in out
    assert "second" not in p._facts                    # nothing written


def test_cap_guard_allows_update_within_cap():
    # Updating an existing key must not be falsely refused even when near full.
    p = _FakePermanent()
    big = "x" * (L1_FACTS_MAX_TOKENS * 4 - 8)
    asyncio.run(promote_fact(p, "blob", big))
    out = asyncio.run(promote_fact(p, "blob", "small"))  # shrink same key
    assert out.startswith("✅") and p._facts["blob"] == "small"


def test_letta_failure_surfaced_not_raised():
    p = _FakePermanent(broken=True)
    out = asyncio.run(promote_fact(p, "user_name", "Takashi"))
    assert out.startswith("❌") and "unavailable" in out  # never raises