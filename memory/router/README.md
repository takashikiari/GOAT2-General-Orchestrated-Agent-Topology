# memory/router/ — Intelligent Memory Router

Drop-in replacement for `MemoryManager.recall` — classifies query intent, routes to
optimal layer(s), adapts routing preferences from observed hit rates and latencies.

## Usage

```python
from memory.router import MemoryRouter

router = MemoryRouter(memory_manager)

# 1) Temporal query → routed to episodic layer (high confidence, single call)
results = await router.search(AgentRole("planner"), "what did I say last week?")

# 2) Recency query → routed to working layer (high confidence, single call)
latest = await router.search(AgentRole("assistant"), "latest conversation summary")

# 3) Short / vague query → fan-out across all layers (low confidence, gather + dedup)
fallback = await router.search(AgentRole("user"), "hello")
```

`MemoryManager.recall()` already calls this automatically. Direct use only needed
when bypassing `MemoryManager`.

## Routing strategy

| Confidence | Layers tried | Execution |
|------------|-------------|-----------|
| ≥ 0.70 | 1 (best match) | single async call |
| 0.40–0.69 | 2 (sequential) | first; fall through if empty |
| < 0.40 | 3 (fan-out) | `asyncio.gather`, deduplicated, newest-first |

## Query type → preferred layer

| Type | Signal | First layer |
|------|--------|-------------|
| `temporal` | "last week", "yesterday", "3 days ago" | episodic |
| `recency` | "latest", "recently", "just now" | working |
| `semantic` | 4+ words, no time signal | episodic |
| `generic` | short, no clear pattern | equal (low confidence → fan-out) |
| `unknown` | empty / 1 word | full fan-out (confidence = 0.0) |

Preferences adapt: `score = 0.70 × affinity + 0.30 × observed_hit_rate`.

## Module map

| File | Responsibility |
|------|----------------|
| `types.py` | `QueryType`, `LayerName`, `Confidence`/`RouteKey`/`Millis` NewTypes; `RoutingDecision`, `LayerTiming`; `CONF_HIGH=0.70`, `CONF_LOW=0.40` |
| `classifier.py` | `classify_query(q) → (QueryType, strength)` — regex, pure, PyO3 candidate |
| `confidence.py` | `compute_confidence(type, strength, hit_rate) → Confidence` — pure |
| `preferences.py` | `preferred_layers(type, stats) → tuple` — affinity + adaptive sort |
| `decision.py` | `make_decision(type, conf, preferred) → RoutingDecision` — pure |
| `cache.py` | `make_route_key()` + `RouteCache` LRU (128 slots) |
| `layer_stats.py` | `LayerStats` (+ p50/p95/p99) + `LayerStatsTracker` — rolling samples, `_percentile` pure helper |
| `executor.py` | `execute_route()` — dispatches per strategy, records `LayerTiming` |
| `router.py` | `MemoryRouter` — assembles pipeline; `search()`, `stats()`, `cache_size` |

## Inspect routing

```python
stats = router.stats()
# {
#   "working":   LayerStats(calls=12, avg_ms=3.2, hit_rate=0.83, p50_ms=2.9, p95_ms=7.1, p99_ms=12.4),
#   "episodic":  LayerStats(...),
#   "long_term": LayerStats(...),
# }
print(router.cache_size)  # cached route patterns
```

## Latency percentiles

`LayerStats` exposes `p50_ms`, `p95_ms`, and `p99_ms` (populated from a rolling
ring buffer of up to 1 000 samples per layer). The underlying `_percentile(samples, p)`
function uses linear interpolation and is a PyO3 candidate. Percentiles read as `0.0`
before any calls. The sample buffer resets if `LayerStatsTracker` is reconstructed
(i.e. on `MemoryRouter` re-instantiation).
