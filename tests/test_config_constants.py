"""tests.test_config_constants — retrieval_budget constants load correctly."""
from memory.config import L3_GAP_SIGNIFICANCE, L3_MIN_GUARANTEE_TOKENS, L3_SIMILARITY_MAX_DISTANCE


def test_l3_guarantee_default():
    assert L3_MIN_GUARANTEE_TOKENS == 1200


def test_similarity_threshold_deprecated_value():
    """L3_SIMILARITY_MAX_DISTANCE is kept for reference; filter now uses gap significance."""
    assert isinstance(L3_SIMILARITY_MAX_DISTANCE, float)
    assert L3_SIMILARITY_MAX_DISTANCE == 1.20


def test_gap_significance_default():
    assert isinstance(L3_GAP_SIGNIFICANCE, float)
    assert L3_GAP_SIGNIFICANCE == 3.0