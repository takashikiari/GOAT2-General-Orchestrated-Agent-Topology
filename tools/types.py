"""tools.types — shared ToolDefinition type for agent-facing tools.

Duck-type compatible with agents.base_agent.ToolDefinition: same field
names, same to_openai() signature. Defined here to avoid a circular
import between tools/ and agents/.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any


@dataclasses.dataclass
class AgentTool:
    """ToolDefinition for agent-facing tools (used by DAG agents, not orchestrator)."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
