"""Backward-compat shim — ToolVerifier moved to tools.dag.validators."""
from tools.dag.validators import VerifierReport, run_tool_verifier

__all__ = ["VerifierReport", "run_tool_verifier"]
