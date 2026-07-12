"""tests.test_config_validator — memory.config_validator dangerous-config guards.

Bug (2026-07-12): two [prefetch]/[reranker] tunables had no validation, unlike
every other config-driven threshold in memory.config_validator:

  * [prefetch] recency_window_days=0 divides-by-zero inside
    rescore_recency (memory/activation.py) — crashes every WARM-served
    turn (see test_rescore_recency_crashes_on_zero_window below, which
    reproduces the crash directly against the pure function as evidence
    independent of the validator).
  * [reranker] top_k=0 slices the reranked candidate list to `[:0]`
    (memory/reranker.py), silently emptying the entire L3 result set every
    turn with no error signal at all.
"""
from __future__ import annotations

import pytest

from memory.config_validator import validate_config


# --- reproduce the crash this validation exists to prevent -------------------

def test_rescore_recency_crashes_on_zero_window(monkeypatch):
    """Failing-test evidence for the recency_window_days=0 bug: with the
    window forced to 0 (what an unvalidated recency_window_days=0 config
    would produce), rescore_recency's own division blows up on every
    warm-served turn."""
    import memory.activation as activation_mod

    monkeypatch.setattr(activation_mod, "PREFETCH_RECENCY_WINDOW_DAYS", 0)
    merged = [{"blended_score": 0.5, "metadata": {"timestamp": 0}}]
    with pytest.raises(ZeroDivisionError):
        activation_mod.rescore_recency(merged, now=100.0)


# --- [prefetch] recency_window_days ------------------------------------------

def test_recency_window_days_zero_raises():
    with pytest.raises(ValueError, match="recency_window_days"):
        validate_config({"prefetch": {"recency_window_days": 0}})


def test_recency_window_days_negative_raises():
    with pytest.raises(ValueError, match="recency_window_days"):
        validate_config({"prefetch": {"recency_window_days": -5}})


def test_recency_window_days_positive_does_not_raise():
    validate_config({"prefetch": {"recency_window_days": 30}})


# --- [reranker] top_k ---------------------------------------------------------

def test_reranker_top_k_zero_raises():
    with pytest.raises(ValueError, match="top_k"):
        validate_config({"reranker": {"top_k": 0}})


def test_reranker_top_k_negative_raises():
    with pytest.raises(ValueError, match="top_k"):
        validate_config({"reranker": {"top_k": -1}})


def test_reranker_top_k_positive_does_not_raise():
    validate_config({"reranker": {"top_k": 20}})


def test_validate_config_with_no_reranker_section_does_not_raise():
    validate_config({})
