"""Backward-compat shim — DagValidator moved to tools.dag.validators."""
from tools.dag.validators import ValidationStatus, validate_results

__all__ = ["ValidationStatus", "validate_results"]
