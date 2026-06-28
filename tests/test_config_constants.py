"""tests.test_config_constants — new retrieval_budget constants load with defaults."""
from memory.config import L3_MIN_GUARANTEE_TOKENS, L3_SIMILARITY_MAX_DISTANCE


def test_l3_guarantee_default():
    assert L3_MIN_GUARANTEE_TOKENS == 1200


def test_similarity_threshold_default():
    assert isinstance(L3_SIMILARITY_MAX_DISTANCE, float)
    assert L3_SIMILARITY_MAX_DISTANCE == 1.0