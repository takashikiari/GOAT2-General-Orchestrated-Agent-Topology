"""memory.wave2 — Phase 2 of memory spec v4: a second retrieval wave over existing metadata (no new table).

After the rerank, the top ``seeds`` results pull in their conversation neighbours: entries of the same
``topic_id`` whose ``sequence_number`` is within ±``window`` of the seed (the turns around it). At most
``per_seed`` per seed, never an entry already in the results. Neighbours are tagged ``mechanisms=["wave2"]``
and ``wave2_of=<seed message_id>`` and scored just below their seed, so they sit right after it; the gap
filter keeps a neighbour only when its seed survived (context_assembler.blended_gap_filter), and the token
budget still applies. Entities are not used: they are stored as a comma-joined string that Chroma's
``where`` cannot match on a substring.
"""
from __future__ import annotations

from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["add_neighbors"]


def _mid(r: dict):
    return (r.get("metadata") or {}).get("message_id") or (r.get("metadata") or {}).get("parent_id")


async def add_neighbors(episodic, ranked: list[dict], seeds: int, per_seed: int, window: int) -> list[dict]:
    """``ranked`` + wave-2 neighbours of its top ``seeds`` (whole-document results, not yet windowed)."""
    if not ranked or seeds <= 0 or per_seed <= 0 or not hasattr(episodic, "neighbors"):
        return ranked
    have = {_mid(r) for r in ranked}
    extra: list[dict] = []
    for seed in ranked[:seeds]:
        meta = seed.get("metadata") or {}
        topic, seq = meta.get("topic_id"), meta.get("sequence_number")
        if not topic or seq is None:
            continue
        try:
            found = await episodic.neighbors(topic, int(seq), window)
        except Exception as exc:  # noqa: BLE001 - wave 2 must never cost the retrieval
            log.warning("wave2: neighbour lookup failed (%s)", exc)
            continue
        found = [n for n in found if _mid(n) not in have]
        found.sort(key=lambda n: abs(int((n.get("metadata") or {}).get("sequence_number", seq)) - int(seq)))
        base = float(seed.get("blended_score", 0.0))
        for k, n in enumerate(found[:per_seed]):
            have.add(_mid(n))
            extra.append(dict(n, blended_score=base - 1e-6 * (k + 1), mechanisms=["wave2"], wave2_of=_mid(seed)))
    if extra:
        log.info("wave2: +%d neighbour(s) from %d seed(s)", len(extra), min(seeds, len(ranked)))
    return ranked + extra
