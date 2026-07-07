"""memory.config_validator — fail-fast validation for memory.toml values.

Called by config._load() when a TOML file is found. Raises ValueError with a
clear message on values that would produce silent incorrect behaviour at
runtime. Only keys present in the user TOML are checked; missing keys fall
back to _DEFAULTS silently (by design).
"""
from __future__ import annotations


def validate_config(cfg: dict) -> None:
    """Raise ValueError if cfg violates a semantic invariant."""
    _check_activation(cfg.get("activation", {}))
    _check_aits(cfg.get("aits", {}))
    _check_retrieval_budget(cfg.get("retrieval_budget", {}))
    _check_prefetch(cfg.get("prefetch", {}))
    _check_working(cfg.get("working", {}))


def _check_activation(act: dict) -> None:
    warm = act.get("drift_warm")
    cold = act.get("drift_cold")
    if warm is not None and cold is not None:
        if float(warm) <= float(cold):
            raise ValueError(
                f"[activation] drift_warm ({warm}) must be > drift_cold ({cold}); "
                "with drift_warm ≤ drift_cold the warm state can never fire "
                "and every turn runs the full three-mechanism prefetch search."
            )
    if cold is not None and float(cold) <= 0:
        raise ValueError(
            f"[activation] drift_cold ({cold}) must be > 0; "
            "a zero-or-negative threshold classifies every turn as cold."
        )
    for key in ("topic_return_threshold", "enriching_sim"):
        val = act.get(key)
        if val is not None and not (0.0 < float(val) <= 1.0):
            raise ValueError(
                f"[activation] {key} ({val}) must be in (0, 1]."
            )


def _check_aits(aits: dict) -> None:
    base = aits.get("budget_base")
    cap = aits.get("budget_hard_cap")
    if base is not None and int(base) <= 0:
        raise ValueError(f"[aits] budget_base ({base}) must be > 0.")
    if base is not None and cap is not None and int(cap) < int(base):
        raise ValueError(
            f"[aits] budget_hard_cap ({cap}) must be >= budget_base ({base})."
        )


def _check_retrieval_budget(rb: dict) -> None:
    frac = rb.get("l3_reserve_fraction")
    if frac is not None:
        f = float(frac)
        if not (0.0 < f < 1.0):
            raise ValueError(
                f"[retrieval_budget] l3_reserve_fraction ({frac}) must be in (0, 1); "
                "0 starves L3, 1 starves L2."
            )
    for key in ("max_results_per_search", "l2_context_cap", "l3_min_guarantee_tokens"):
        val = rb.get(key)
        if val is not None and int(val) <= 0:
            raise ValueError(f"[retrieval_budget] {key} ({val}) must be > 0.")


def _check_prefetch(pf: dict) -> None:
    t = pf.get("timeout")
    if t is not None and float(t) <= 0:
        raise ValueError(f"[prefetch] timeout ({t}) must be > 0.")
    mr = pf.get("max_results")
    if mr is not None and int(mr) <= 0:
        raise ValueError(f"[prefetch] max_results ({mr}) must be > 0.")


def _check_working(wk: dict) -> None:
    mm = wk.get("max_messages")
    if mm is not None and int(mm) <= 0:
        raise ValueError(f"[working] max_messages ({mm}) must be > 0.")
