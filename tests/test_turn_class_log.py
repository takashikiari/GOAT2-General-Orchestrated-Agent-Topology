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
    assert act.classify_turn("x", _A([1.0, 0.0]), [1.0, 0.0], lane="channel") == "warm"
    assert act.classify_turn("x", _A([1.0, 0.0]), [1.0, 0.0], lane="dag") == "warm"
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn_class")]
    assert msgs[0] == "turn_class state=cold kind=human cosine=- lex=-"
    assert msgs[1] == "turn_class state=warm kind=human cosine=1.000 lex=-"
    assert "kind=channel" in msgs[2]
    assert "kind=dag" in msgs[3]


def test_kind_is_the_given_lane_not_a_guess_from_the_text(caplog):
    """Channel turns reach classify_turn with the digest header stripped (retrieval_query), so
    sniffing the prefix logged every one as human; the lane now comes from the caller."""
    caplog.set_level(logging.INFO, logger="memory.activation")
    act.classify_turn(CHANNEL_DIGEST_PREFIX + ", x", _A([1.0, 0.0]), [1.0, 0.0])
    act.classify_turn("textul mesajelor, fara antet", _A([1.0, 0.0]), [1.0, 0.0], lane="channel")
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn_class")]
    assert "kind=human" in msgs[0]     # no lane given: human, whatever the text says
    assert "kind=channel" in msgs[1]
