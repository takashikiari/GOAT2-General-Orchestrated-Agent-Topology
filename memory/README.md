# memory/ — Three-Tier Memory System

```python
from memory.memory_manager import memory_manager, MemoryType
from memory.router import MemoryRouter
```

## Three tiers

| Tier | Backend | Search | Scope |
|------|---------|--------|-------|
| `WORKING` | DictBackend / Redis | keyword token overlap | process + TTL 1 h |
| `EPISODIC` | ChromaDB HNSW | semantic cosine | session, disk |
| `LONG_TERM` | Letta REST API | semantic server-side | cross-session |

`recall()` routes through `MemoryRouter` (intelligent). `search(memory_type=X)` goes direct.

## Key behaviours

- **Temporal search** — `search(start_datetime="yesterday morning")` applies post-filter via
  `filter_by_time`; natural language parsed by `time_parser` (default TZ: Europe/Bucharest).
  `timeline(start, end)` lists entries in a range; `recent(limit)` returns newest first;
  `debug_trace(query)` returns per-tier JSON counts. Entries without `created_at_ts` are
  excluded (never assumed to fall in range) — legacy records are never invented.
- **Routed recall** — `recall(role, query)` → `MemoryRouter.search()`: classify intent,
  confidence ≥0.70 → 1 layer, 0.40–0.69 → 2 sequential, <0.40 → full fan-out.
- **Letta fallback** — `LettaHealthProbe` checks every 30 s; `_InContextFallback` handles
  all ops when Letta is unreachable. Auto-reconnects.
- **TTL priority** in `WorkingMemoryLayer.store()`: `ttl=` kwarg → `metadata["ttl"]` →
  `default_ttl` (3600 s) → `ttl=0` = no expiry.
- **Letta blocks** — each role agent has `persona` (behavioral style) and `human` (user
  profile) core-memory blocks, both empty on creation.
- **Fact confidence** — `info_extract.maybe_store_info` classifies each extracted fact as
  `explicit` (direct user statement) or `inferred` (deduced). Only explicit facts reach
  Letta core via `PollutionGuard`; inferred facts go to ChromaDB tagged `"inferred"` with
  `expires_at_ts = now + 7 days` in metadata.
- **Pollution guard** — `PollutionGuard.validate(key, value, kind, existing_block)` blocks
  inferred facts and technical keys, and flags conflicts (existing key with different value)
  at `WARNING` without auto-overwriting. The underlying `validate_fact()` is pure (PyO3 candidate).

## Module map

### Shared
| `types.py` | NewTypes, `MemoryEntry`, `MemoryLayer` Protocol, `MemoryEntryMetadata` |

### Letta (long-term)
| `letta_helpers.py` | constants, TypedDicts, PyO3-candidate pure helpers |
| `letta_fallback.py` | `_InContextFallback` — keyword store when Letta is down |
| `letta_health.py` | `LettaHealthProbe` — HTTP client + 30 s cooldown |
| `letta_registry.py` | `LettaAgentRegistry` — lazy create/cache one agent per role; creates `persona` + `human` blocks |
| `letta_ops_*.py` | `do_store`, `do_retrieve`, `do_search`, `do_list`, `do_clear` |
| `letta_blocks.py` | `do_get_block`, `do_set_block` — core-memory block CRUD |
| `letta_client.py` | `LettaClient` — thin coordinator + singleton |

### Working memory
| `working_backend.py` | `StorageBackend` Protocol |
| `dict_backend.py` | `DictBackend` — in-process dict, lazy TTL |
| `redis_backend.py` | `RedisBackend` — drop-in, requires `redis[hiredis]≥5.0` |
| `working_memory.py` | `WorkingMemoryLayer` + `working_memory` singleton |

### ChromaDB (episodic)
| `chroma_types.py` | constants + TypedDicts |
| `chroma_helpers.py` / `chroma_parsers.py` | PyO3-candidate pure transforms |
| `chromadb_client.py` | `ChromaMemoryClient` + `chroma_client` singleton |

### Quality control
| `pollution_guard.py` | `validate_fact()` (pure, PyO3 candidate) + `PollutionGuard` — blocks inferred facts from Letta core, detects key conflicts, logs at WARNING |

### Temporal search
| `time_parser.py` | `parse_time_range(expr)` — natural language + ISO 8601 → `(start_epoch, end_epoch)` |
| `temporal_filter.py` | `filter_by_time(entries, start_ts, end_ts)` · `resolve_range(start_expr, end_expr)` |
| `temporal_list.py` | `gather_tier_list(layers, role, tier, limit)` — fan-out list with deduplication |
| `temporal_search.py` | `TemporalSearchMixin` — `timeline()`, `recent()`, `debug_trace()` |

### Orchestration
| `memory_enums.py` | `MemoryType` enum, `LayerStatus` |
| `memory_crud.py` / `memory_search.py` / `memory_promote.py` | mixins |
| `memory_manager.py` | `MemoryManager` — `recall()` via `MemoryRouter`; gains `timeline`, `recent`, `debug_trace` from `TemporalSearchMixin` |

### Router (`router/`)
See `memory/router/README.md`. Entry point: `MemoryRouter(memory_manager)`.
