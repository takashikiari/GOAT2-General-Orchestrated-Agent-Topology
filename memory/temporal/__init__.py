"""Temporal search and filtering utilities.

Provides time-based search and filtering for memory entries.
Used for timeline queries and historical memory access.

EXPORTS:
- filter_by_time: Filter entries by time range
- resolve_range: Parse time range strings
- parse_time_range: Parse natural language time ranges
- gather_tier_list: Cross-tier list with dedup
- TemporalSearchMixin: timeline / recent / debug_trace mixin
"""
from __future__ import annotations

import logging

from memory.temporal.temporal_filter import filter_by_time, resolve_range
from memory.temporal.time_parser import parse_time_range
from memory.temporal.temporal_list import gather_tier_list
from memory.temporal.temporal_search import TemporalSearchMixin

log = logging.getLogger("goat2.memory.temporal")

__all__ = [
    "filter_by_time",
    "resolve_range",
    "parse_time_range",
    "gather_tier_list",
    "TemporalSearchMixin",
]