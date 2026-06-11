"""System tools — calculator, think, and shell operations.

This module provides system utility tools:
- CALCULATOR: Safe AST-based arithmetic expression evaluator
- THINK: Chain-of-thought reasoning tool (pure, no I/O)
- SHELL: Restricted shell command execution for DAG agents

TOOL EXPORTS:
============
- CALCULATOR: Evaluate math expressions (+, -, *, /, //, %, **)
- THINK: Record internal reasoning steps
- SHELL: Execute basic read-only shell commands (DAG agents only)
"""

from __future__ import annotations

import logging

from tools.system.calculator import CALCULATOR
from tools.system.shell_tool import SHELL
from tools.system.think import THINK
from tools.system.log_reader import READ_LOGS

log = logging.getLogger("goat2.tools.system")

__all__ = ["THINK", "CALCULATOR", "SHELL", "READ_LOGS"]