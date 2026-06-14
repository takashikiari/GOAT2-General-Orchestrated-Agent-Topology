# Working Memory

Session-scoped, fast, ephemeral storage for active conversation context, tool
outputs, and DAG agent coordination. It is the WORKING tier of GOAT 2.0's
three-tier memory (WORKING → EPISODIC → LONG_TERM).

## Architecture

```
WorkingMemoryLayer  (working_memory.py)
  ├─ WorkingCrudMixin   (working_crud.py)   store / retrieve / delete / clear / health / list
  ├─ WorkingQueryMixin  (working_query.py)  search / list / ttl_of / count
  └─ WorkingSweepMixin  (working_sweep.py)  proactive TTL eviction
        └─ backend: WorkingMemoryBackend   (a storage detail, injected)
```

The layer holds **no** storage logic — it delegates to a backend. Records are
serialized to `RecordDict` (`working_record.py`) on the wire.

## Backend Protocol — swapping backends

The backend is a **detail, not architecture**. Any object that satisfies the
`WorkingMemoryBackend` Protocol (`backend_protocol.py`) can be used — no
inheritance required, conformance is structural (`@runtime_checkable`):

```python
async def get(self, agent_role: str, key: str) -> dict | None: ...
async def set(self, agent_role: str, key: str, value: dict, expires_at: float | None) -> None: ...
async def delete(self, agent_role: str, key: str) -> bool: ...
async def keys(self, agent_role: str) -> list[str]: ...
async def scan(self, agent_role: str, pattern: str) -> list[str]: ...
async def flush(self, agent_role: str) -> int: ...
async def ping(self) -> bool: ...
```

To swap storage, implement those seven async methods and pass an instance:

```python
WorkingMemoryLayer(backend=MyBackend())
```

Two backends ship today: `DictBackend` (in-process, zero deps) and `RedisBackend`
(networked). Both satisfy the Protocol; **neither name leaks into the
abstraction**. The legacy `StorageBackend` Protocol (`working_backend.py`) is
retained for backward compatibility and coexists with `WorkingMemoryBackend`.

## Capacity management (`capacity.py`)

Working memory is bounded to **50 entries per `agent_role`**:

- Count `>= 45` → logged at **WARNING** (approaching limit).
- Count `>= 50` → the **oldest** promotable entries (by `created_at_ts`) are
  promoted to the episodic tier and removed from working memory **before** the
  new write, so the tier stays at/under 50.
- Promotion order is **oldest first**.

Enforced from the WORKING branch of `MemoryManager.store` (`memory_crud.py`) and
never fatal — a capacity error is logged and the write proceeds.

### DAG namespace isolation

Keys in the `dag:` namespace (DAG coordination state) are **never auto-promoted**.
They are excluded from promotion and expire via their own TTL. Only
conversational/turn entries (everything not prefixed `dag:`) are promotable.

## Full context injection

`supervisor/session/mem_inject.py` injects a `[Working Memory]` block containing
**all** live working entries (up to 50), unfiltered by semantic similarity, so
GOAT has complete session awareness each turn:

```
[Working Memory]
- turn_001 (2026-06-14 09:00): User said X
- turn_002 (2026-06-14 09:01): GOAT replied Y
- dag:session123:progress (2026-06-14 09:01): Wave 2/4 complete
```

This is appended to the existing cross-tier `[Memory]` semantic fan-out.

## Timestamp schema

Every working entry (`RecordDict`) carries:

| Field            | Type          | Meaning                                   |
|------------------|---------------|-------------------------------------------|
| `created_at`     | ISO 8601 str  | When the entry was first written          |
| `created_at_ts`  | Unix float    | Same, epoch seconds (capacity ordering)   |
| `updated_at`     | ISO 8601 str  | Last write time                           |
| `updated_at_ts`  | Unix float    | Same, epoch seconds                       |
| `accessed_at_ts` | Unix float    | Last read time (bumped on every `retrieve`)|
| `access_count`   | int           | Number of reads (incremented on `retrieve`)|

`created_at*` is set on store; `updated_at*` / `accessed_at*` / `access_count`
are set on store and the access fields are bumped on `retrieve` (the record is
written back preserving its original `expires_at`, so reads never alter TTL). The
trailing fields are `NotRequired` — records written before this schema still load.
Capacity uses `created_at_ts` to pick the oldest entries.

> Note: raw `backend.get` (used on DAG hot paths) stays pure and does **not** bump
> access counters — only the `WorkingMemoryLayer.retrieve` path does.
