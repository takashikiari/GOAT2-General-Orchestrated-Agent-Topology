"""Tests for BUG-? fix: GOAT_SYSTEM includes today's date.

The LLM used to fabricate year-2025 timestamps when asked
about "this morning" because the system prompt had no
date anchor. The fix prepends "Today's date: YYYY-MM-DD" so
the LLM grounds its temporal reasoning in the actual day.

See `supervisor/identity.py:GOAT_SYSTEM` — the date prefix
is generated at import time so it's always fresh for the
current process.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone


def test_goat_system_includes_today_date():
    """The system prompt must include today's date so the LLM
    can ground its temporal reasoning."""
    from supervisor.identity import GOAT_SYSTEM
    # Pattern: "Today's date: YYYY-MM-DD" at the very start.
    assert GOAT_SYSTEM.startswith("Today's date:"), (
        f"GOAT_SYSTEM must start with 'Today's date:' anchor; "
        f"got: {GOAT_SYSTEM[:80]!r}"
    )
    # The date should be the actual today.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today in GOAT_SYSTEM, (
        f"GOAT_SYSTEM must include today's date ({today}); "
        f"got: {GOAT_SYSTEM[:200]!r}"
    )


def test_today_date_is_in_iso_format():
    """The date prefix is YYYY-MM-DD so the LLM can parse it
    unambiguously."""
    from supervisor.identity import GOAT_SYSTEM
    match = re.match(r"Today's date: (\d{4}-\d{2}-\d{2})", GOAT_SYSTEM)
    assert match, (
        f"GOAT_SYSTEM must start with 'Today's date: YYYY-MM-DD'; "
        f"got: {GOAT_SYSTEM[:80]!r}"
    )
    year, month, day = map(int, match.group(1).split("-"))
    now = datetime.now(timezone.utc)
    # Year should be the current year (or ±1 for timezones).
    assert abs(year - now.year) <= 1, (
        f"Year in GOAT_SYSTEM ({year}) should be current year "
        f"({now.year}) ±1 for timezone tolerance"
    )
    # Month/day should be valid.
    assert 1 <= month <= 12
    assert 1 <= day <= 31


def test_operational_rules_still_present():
    """The original GOAT_SYSTEM rules (1-10) must still be in
    the prompt after we prepend the date anchor."""
    from supervisor.identity import GOAT_SYSTEM
    # Spot-check: rule 1 ("Never invent facts") must still be there.
    assert "Never invent facts" in GOAT_SYSTEM
    # And rule 10 ("Prefer most recent verified information").
    assert "Prefer most recent verified information" in GOAT_SYSTEM
