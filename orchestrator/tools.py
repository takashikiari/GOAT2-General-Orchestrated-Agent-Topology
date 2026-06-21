"""orchestrator.tools — ToolDefinition wraps an async handler with its OpenAI-compatible schema."""
from __future__ import annotations


class ToolDefinition:
    """Describes one callable tool: LLM-facing name/description/schema + async handler.

    The handler must be an async callable whose keyword arguments match the
    tool's parameter schema and whose return value is str()-convertible.
    """

    def __init__(self, name: str, description: str, parameters: dict, handler) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    def to_openai_schema(self) -> dict:
        """Return {"type": "function", "function": {...}} as the OpenAI-compatible API expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
