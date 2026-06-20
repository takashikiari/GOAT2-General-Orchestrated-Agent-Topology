# Task 1 Report: Episodic recall cache tunables

## What I implemented

Added three `Final`-typed constants to `/home/lenovo/workspace/goat2/config/limits.py` and extended the `__all__` list.

**1. `__all__` extension** (inside the existing Faza 2 comment block, right after `"DEFAULT_EPISODIC_TOP_K"`):

```python
# Faza 2 Commit 2: episodic recall cache tunables.
"EPISODIC_CACHE_MAX_SIZE",
"EPISODIC_CACHE_TTL_S",
"EPISODIC_CACHE_TURN_BUCKET",
```

**2. Constant definitions** (placed immediately after `DEFAULT_EPISODIC_TOP_K`, keeping the Faza 2 family together):

```python
# Faza 2 Commit 2: episodic recall cache tunables.
EPISODIC_CACHE_MAX_SIZE: Final[int] = 256
"""LRU cap for the episodic recall cache (entries)."""

EPISODIC_CACHE_TTL_S: Final[float] = 60.0
"""Per-entry TTL for cached episodic recall results (seconds)."""

EPISODIC_CACHE_TURN_BUCKET: Final[int] = 5
"""Refresh the cache bucket every N turns — bounds staleness without a per-query clock."""
```

Each constant uses `Final` typing as required and includes a docstring explaining its purpose — consistent with the style of neighbouring constants in the Faza 2 block and the project's magic-numbers policy.

## Verification command output

```
$ python3 -c "from config.limits import EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET; print(EPISODIC_CACHE_MAX_SIZE, EPISODIC_CACHE_TTL_S, EPISODIC_CACHE_TURN_BUCKET)"
256 60.0 5
```

Expected `256 60.0 5` — matches.

(Note: the brief used `python`, but the host shell only exposes `python3`. Used the available interpreter; semantics are identical.)

## Files changed

- `/home/lenovo/workspace/goat2/config/limits.py` — 14 insertions, 0 deletions.

```bash
$ git show --stat HEAD
commit bc3f95ec5c95011aca4008456c0726ec6da8401b
    feat(config): Faza 2 Commit 2 — episodic recall cache tunables
 config/limits.py | 14 ++++++++++++++
 1 file changed, 14 insertions(+)
```

No other files touched.

## Self-review findings

- [x] Added the three constants exactly with `Final` typing (`Final[int]`, `Final[float]`, `Final[int]`).
- [x] Extended `__all__` inside the existing Faza 2 group, immediately after `"DEFAULT_EPISODIC_TOP_K"`.
- [x] Verification command printed `256 60.0 5` (the brief's expected output).
- [x] Commit message is `feat(config): Faza 2 Commit 2 — episodic recall cache tunables` — exact match.
- [x] Only `config/limits.py` changed; no other file or section was touched.

All self-review checks pass. No issues found.

## Concerns

None. The implementation is a clean transcription of the brief with the values exactly as specified.
