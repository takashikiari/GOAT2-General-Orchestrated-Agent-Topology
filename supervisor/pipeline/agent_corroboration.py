"""Backward-compat shim — AgentCorroboration moved to tools.dag.validators."""
from tools.dag.validators import CorroborationReport, check_corroboration

__all__ = ["CorroborationReport", "check_corroboration"]
