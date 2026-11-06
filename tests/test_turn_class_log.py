import logging

from memory import activation as act
from memory.temporal_route import CHANNEL_DIGEST_PREFIX


class _A:
    def __init__(self, centroid, recent=()):
        self.centroid, self.recent_queries = centroid, list(recent)


def test_states_unchanged_and_logged_with_kind(caplog):
    caplog.set_level(logging.INFO, logger="memory.activation")
    assert act.classify_turn("salut", None, [1.0, 0.0]) == "cold"
    assert act.classify_turn("salut", _A([1.0, 0.0]), [1.0, 0.0]) == "warm"
    assert act.classify_turn(CHANNEL_DIGEST_PREFIX + ", x", _A([1.0, 0.0]), [1.0, 0.0]) == "warm"
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn_class")]
    assert msgs[0] == "turn_class state=cold kind=human cosine=- lex=-"
    assert msgs[1] == "turn_class state=warm kind=human cosine=1.000 lex=-"
    assert "kind=digest" in msgs[2]
