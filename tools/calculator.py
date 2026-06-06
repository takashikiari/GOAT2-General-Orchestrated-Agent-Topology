"""Safe arithmetic expression evaluator using AST parsing.

Provides a single ToolDefinition (CALCULATOR) that evaluates mathematical
expressions (+, -, *, /, //, %, **) via Python's AST, preventing arbitrary
code execution. Exponentiation is capped to avoid resource exhaustion.
"""

from __future__ import annotations

import ast
import operator
from typing import Final

from agents.base_agent import ToolDefinition

__all__ = ["CALCULATOR"]

_MAX_EXP: Final[int] = 1_000  # guard: 2**999999999 would freeze the process

# Allowlist — only these AST node types reach _eval_node. No exec, no import, no call.
_OPS: Final[dict] = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.UAdd:     operator.pos,
    ast.USub:     operator.neg,
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": "A math expression, e.g. '(3 + 4) * 2 / 7' or '2 ** 10'.",
        },
    },
    "required": ["expression"],
}


def _eval_node(node: ast.expr) -> float:
    """Walk an AST expression node; raise ValueError for any unsupported construct."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        left  = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXP:
            raise ValueError(f"Exponent magnitude exceeds limit ({_MAX_EXP})")
        op = _OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


async def _handler(expression: str) -> str:
    """Evaluate expression; return numeric result or ERROR: <reason> on failure."""
    try:
        tree   = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        return str(int(result)) if isinstance(result, float) and result.is_integer() else str(result)
    except ZeroDivisionError:
        return "ERROR: division by zero"
    except Exception as exc:
        return f"ERROR: {exc}"


CALCULATOR = ToolDefinition(
    name="calculator",
    description="Safely evaluate a math expression. Supports +, -, *, /, //, %, ** (no code execution).",
    parameters=_SCHEMA,
    handler=_handler,
)
