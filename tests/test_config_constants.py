"""tests.test_config_constants — retrieval_budget constants load correctly."""
from memory.config import (
    ACTIVATION_DRIFT_COLD,
    ACTIVATION_LEXICAL_LOW,
    L3_GAP_SIGNIFICANCE,
    L3_MIN_GUARANTEE_TOKENS,
    PREFETCH_RECENCY_BASE_WEIGHT,
    PREFETCH_RECENCY_RECENCY_WEIGHT,
)


def test_l3_guarantee_default():
    assert L3_MIN_GUARANTEE_TOKENS == 1200


def test_gap_significance_default():
    assert isinstance(L3_GAP_SIGNIFICANCE, float)
    assert L3_GAP_SIGNIFICANCE == 3.0


# --- bug 3 (2026-07-12): rescore_recency blend weights are now config-driven -

def test_recency_blend_weights_load_from_config():
    assert PREFETCH_RECENCY_BASE_WEIGHT == 0.7
    assert PREFETCH_RECENCY_RECENCY_WEIGHT == 0.3


def test_recency_blend_weights_sum_to_one():
    assert abs((PREFETCH_RECENCY_BASE_WEIGHT + PREFETCH_RECENCY_RECENCY_WEIGHT) - 1.0) < 1e-9


# --- bug 1b (2026-07-12): recalibrated activation thresholds -----------------

def test_activation_drift_cold_recalibrated():
    assert ACTIVATION_DRIFT_COLD == 0.30


def test_activation_lexical_low_recalibrated():
    assert ACTIVATION_LEXICAL_LOW == 0.10