"""Backward-compat shim — GoatValidator moved to tools.dag.validators."""
from tools.dag.validators import (
    ValidationReport,
    validate_dag_result,
    validate_dag_result_simple,
)

__all__ = ["ValidationReport", "validate_dag_result", "validate_dag_result_simple"]
