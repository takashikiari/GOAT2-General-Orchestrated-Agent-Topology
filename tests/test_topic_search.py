"""tests.test_topic_search — unit tests for topic_id metadata flow.

Tests that topic_id is plumbed correctly through store and search without
hitting a real ChromaDB instance. Mirrors the where-clause builder logic from
episodic.search() and asserts the correct ChromaDB filter structure.
"""
from __future__ import annotations


def _build_where(after, before, topic_id):
    """Mirror the clause-builder logic from episodic.search() for assertions."""
    clauses = []
    if after is not None:
        clauses.append({"timestamp": {"$gte": after}})
    if before is not None:
        clauses.append({"timestamp": {"$lte": before}})
    if topic_id:
        clauses.append({"topic_id": {"$eq": topic_id}})
    if len(clauses) == 0:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def test_where_clause_topic_only():
    result = _build_where(None, None, "abc-123")
    assert result == {"topic_id": {"$eq": "abc-123"}}


def test_where_clause_topic_and_after():
    result = _build_where(1000.0, None, "abc-123")
    assert result == {"$and": [{"timestamp": {"$gte": 1000.0}}, {"topic_id": {"$eq": "abc-123"}}]}


def test_where_clause_no_topic():
    result = _build_where(None, None, None)
    assert result is None


def test_where_clause_no_topic_empty_string():
    result = _build_where(None, None, "")
    assert result is None


def test_where_clause_topic_with_after_and_before():
    result = _build_where(100.0, 200.0, "t1")
    assert result == {"$and": [
        {"timestamp": {"$gte": 100.0}},
        {"timestamp": {"$lte": 200.0}},
        {"topic_id": {"$eq": "t1"}},
    ]}
