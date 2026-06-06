"""LRU routing-decision cache keyed by hashed query pattern — zero external dependencies."""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Final

from memory.router.types import QueryType, RoutingDecision, RouteKey

__all__ = ["RouteCache", "make_route_key"]

_CACHE_MAXSIZE: Final[int] = 128
_KEY_WORDS:     Final[int] = 5     # significant words hashed into the cache key
_STOP_WORDS:    Final[frozenset[str]] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "what", "how", "can", "do", "did", "does", "i", "me", "my",
    "in", "on", "at", "to", "of", "for", "with",
})


def make_route_key(query: str, query_type: QueryType) -> RouteKey:
    """
    Hash the first _KEY_WORDS non-stopword tokens + query_type into a short hex key.
    Similar phrasings of the same intent produce the same RouteKey.
    Pure — same inputs always produce the same RouteKey. PyO3 candidate.
    """
    tokens = [w for w in query.lower().split() if w not in _STOP_WORDS][:_KEY_WORDS]
    raw = f"{query_type}:{' '.join(tokens)}"
    return RouteKey(hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12])


class RouteCache:
    """Thread-unsafe LRU cache mapping RouteKey → RoutingDecision (asyncio-safe)."""

    def __init__(self, maxsize: int = _CACHE_MAXSIZE) -> None:
        self._data: OrderedDict[RouteKey, RoutingDecision] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: RouteKey) -> RoutingDecision | None:
        """Return cached decision with cached=True set, or None on miss."""
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        d = self._data[key]
        return RoutingDecision(d.layers, d.confidence, d.query_type, cached=True)

    def put(self, key: RouteKey, decision: RoutingDecision) -> None:
        """Insert or refresh a decision; evict the least-recently-used entry when full."""
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = decision
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    @property
    def size(self) -> int:
        """Number of cached routing decisions."""
        return len(self._data)
