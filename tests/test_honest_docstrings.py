"""Tests for BUG-031: honest docstrings.

The audit found docstrings that claimed things the code did
not actually do — most notably freshness.py "no hardcoded
numbers" alongside a ``_DEFAULTS`` dict. The fix is to keep
the docstrings aligned with the implementation, and to verify
the alignment with a small structural test.

The policy is documented in docs/regex_policy.md (the regex
analogue of this audit) and in the new docs/docstring_policy.md
introduced here.
"""
from __future__ import annotations

import asyncio
import importlib
import logging


# ── freshness.py docstring ────────────────────────────────────────────────


def test_freshness_docstring_admits_defensive_defaults():
    """The freshness docstring must not claim 'no hardcoded
    numbers' — the module does carry a _DEFAULTS dict as a
    defensive fallback when toml is missing."""
    from supervisor.mechanisms import freshness
    doc = freshness.__doc__ or ""
    assert "no hardcoded numbers" not in doc, (
        "freshness docstring lies — the module DOES carry "
        "_DEFAULTS. Use 'defensive fallback' wording instead."
    )
    assert "defensive" in doc.lower(), (
        "freshness docstring should acknowledge the defensive "
        "fallback so operators know the actual contract."
    )


# ── Generic "never raises" claim ──────────────────────────────────────────


def _docstring_claims_never_raises(doc: str) -> bool:
    """True when the docstring explicitly says the function
    never raises (case-insensitive substring match)."""
    doc_lc = doc.lower()
    needles = ["never raises", "no exception", "cannot raise", "never throw"]
    return any(n in doc_lc for n in needles)


def test_non_async_helpers_docstrings_do_not_claim_never_raises():
    """Pure helper functions that don't catch exceptions should
    not claim 'never raises' — they are NOT exception-safe.

    The audit found a few functions with overly strong docstring
    claims. We pin down the strongest negative case here: the
    text_match helpers in utils/ do NOT claim 'never raises'."""
    import utils.text_match as tm
    for name in ("substring_match", "token_match",
                 "balanced_extract", "extract_quoted_field",
                 "find_all_substrings", "prefix_match"):
        fn = getattr(tm, name, None)
        if fn is None:
            continue
        doc = fn.__doc__ or ""
        assert not _docstring_claims_never_raises(doc), (
            f"utils.text_match.{name} docstring claims 'never "
            f"raises' — that's misleading for a pure helper."
        )


# ── "Best-effort" callers must actually be best-effort ──────────────────


def test_style_sync_refresh_is_best_effort():
    """refresh_style is documented 'best-effort; never raises'.
    Verify the function actually catches all exceptions."""
    from supervisor.mechanisms.style_sync import refresh_style

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("broken")

    sv = type("S", (), {
        "memory_manager": Broken(),
        "_behavior_style": "",
    })()
    # Must NOT raise — refresh_style is documented as best-effort.
    asyncio.run(refresh_style(sv))


def test_turn_persistence_store_and_promote_is_best_effort():
    """store_and_promote is documented 'best-effort; never raises'."""
    from supervisor.session.turn_persistence import store_and_promote

    class Broken:
        async def store(self, *a, **kw):
            raise RuntimeError("redis down")
        async def working(self):
            raise RuntimeError("nope")
        async def promote_turns(self, *a, **kw):
            raise RuntimeError("nope")

    sv = type("S", (), {"memory_manager": Broken()})()
    # Must NOT raise — store_and_promote is best-effort.
    asyncio.run(store_and_promote(sv, turn_count=1, intent="x", summary="y"))


def test_load_style_is_best_effort():
    """load_style is documented as best-effort."""
    from supervisor.behavior.store import load_style

    class Broken:
        async def get_block(self, *a, **kw):
            raise RuntimeError("letta down")

    asyncio.run(load_style(Broken()))
    # No exception means best-effort is honoured.
