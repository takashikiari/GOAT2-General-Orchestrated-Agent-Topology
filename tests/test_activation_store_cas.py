"""tests.test_activation_store_cas — ActivationStore.set write-race guard.

Bug (2026-07-12): the post-turn prefetch (orchestrator/prefetch.py) is a
fire-and-forget ``asyncio.create_task`` with no timeout by design — "has as
long as it needs before the user sends their next message". If turn N's
prefetch is slow and finishes AFTER turn N+1's already-faster prefetch has
written, a plain Redis SET/SETEX would let turn N's write silently clobber
turn N+1's fresher activation (topic_id, centroid, merged L3 results,
turn_count) with stale data — turn N+2 then warm-serves the wrong blob.

``ActivationStore.set`` now runs a single atomic Redis EVAL (Lua script) that
compares the incoming write's ``Activation.ts`` (the turn's ORIGIN timestamp,
not this write's completion time — see ``update_activation``'s
``turn_start``) against whatever is currently stored, and rejects the write
if the stored value is strictly newer. These tests exercise the real Lua
script against a local Redis (atomicity can't be honestly verified against a
mock — a mock can't prove the read-compare-write has no race window of its
own); skipped if Redis is unreachable. All I/O (including cleanup) stays
inside a single ``asyncio.run`` per test — the lazily-created redis.asyncio
client is bound to the event loop that first used it, so a second
``asyncio.run`` call would hand it a closed loop.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from memory.activation import Activation, ActivationStore
from memory.working import WorkingMemory


def _redis_available() -> bool:
    try:
        import redis
        return bool(redis.Redis(host="localhost", port=6379, socket_connect_timeout=0.5).ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="requires a local Redis on localhost:6379")


def _chat_id() -> str:
    return f"test-cas-{uuid.uuid4()}"


def test_older_write_does_not_clobber_newer():
    """The slow turn N write must lose to the already-written turn N+1."""
    chat_id = _chat_id()
    newer = Activation(centroid=[1.0, 0.0], turn_count=5, ts=200.0, topic_id="new-topic")
    older = Activation(centroid=[0.0, 1.0], turn_count=1, ts=100.0, topic_id="stale-topic")

    async def run():
        store = ActivationStore(WorkingMemory(), ttl_seconds=60)
        try:
            # Turn N+1 (faster) writes first...
            applied_newer = await store.set(chat_id, newer)
            # ...then turn N's slow prefetch finally lands, out of order.
            applied_older = await store.set(chat_id, older)
            stored = await store.get(chat_id)
            return applied_newer, applied_older, stored
        finally:
            await store.clear(chat_id)

    applied_newer, applied_older, stored = asyncio.run(run())
    assert applied_newer is True
    assert applied_older is False
    assert stored is not None
    assert stored.topic_id == "new-topic"
    assert stored.turn_count == 5


def test_newer_write_after_older_succeeds():
    """Normal in-order writes are unaffected by the guard."""
    chat_id = _chat_id()
    older = Activation(centroid=[0.0, 1.0], turn_count=1, ts=100.0, topic_id="first")
    newer = Activation(centroid=[1.0, 0.0], turn_count=2, ts=200.0, topic_id="second")

    async def run():
        store = ActivationStore(WorkingMemory(), ttl_seconds=60)
        try:
            a1 = await store.set(chat_id, older)
            a2 = await store.set(chat_id, newer)
            stored = await store.get(chat_id)
            return a1, a2, stored
        finally:
            await store.clear(chat_id)

    a1, a2, stored = asyncio.run(run())
    assert a1 is True
    assert a2 is True
    assert stored is not None
    assert stored.topic_id == "second"


def test_equal_ts_write_is_not_rejected():
    """Same-turn re-writes (e.g. the synchronous enriching-write refresh,
    which mutates and re-saves the SAME activation object without bumping
    ``ts``) must not be treated as stale."""
    chat_id = _chat_id()
    act = Activation(centroid=[1.0, 0.0], turn_count=3, ts=150.0, topic_id="t1", merged=[])

    async def run():
        store = ActivationStore(WorkingMemory(), ttl_seconds=60)
        try:
            a1 = await store.set(chat_id, act)
            act.merged = [{"content": "folded in"}]
            a2 = await store.set(chat_id, act)
            stored = await store.get(chat_id)
            return a1, a2, stored
        finally:
            await store.clear(chat_id)

    a1, a2, stored = asyncio.run(run())
    assert a1 is True
    assert a2 is True
    assert stored is not None
    assert stored.merged == [{"content": "folded in"}]


def test_first_write_with_no_prior_value_always_applied():
    chat_id = _chat_id()
    act = Activation(centroid=[1.0, 0.0], ts=50.0, topic_id="only")

    async def run():
        store = ActivationStore(WorkingMemory(), ttl_seconds=60)
        try:
            applied = await store.set(chat_id, act)
            stored = await store.get(chat_id)
            return applied, stored
        finally:
            await store.clear(chat_id)

    applied, stored = asyncio.run(run())
    assert applied is True
    assert stored is not None
    assert stored.topic_id == "only"


def test_corrupt_stored_blob_never_blocks_a_write():
    """A corrupt/undecodable stored blob is treated like 'nothing stored'."""
    chat_id = _chat_id()
    act = Activation(centroid=[1.0, 0.0], ts=999.0, topic_id="fresh")

    async def run():
        store = ActivationStore(WorkingMemory(), ttl_seconds=60)
        try:
            client = store._working._get_client()
            await client.set(store._key(chat_id), "not valid json{{{")
            applied = await store.set(chat_id, act)
            stored = await store.get(chat_id)
            return applied, stored
        finally:
            await store.clear(chat_id)

    applied, stored = asyncio.run(run())
    assert applied is True
    assert stored is not None
    assert stored.topic_id == "fresh"
