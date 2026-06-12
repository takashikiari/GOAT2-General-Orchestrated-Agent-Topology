"""
GOAT 2.0 — CoderAgent

Writes production-quality code and validates its own output. Defaults to
deepseek-coder for strong code generation. Includes a built-in
`validate_syntax` tool so the model can self-check before returning.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import TYPE_CHECKING

from config.settings import ModelSpec, Settings
from .base_agent import BaseAgent, tool

if TYPE_CHECKING:
    # Cross-module type hints only — keeps agents/ decoupled at runtime.
    from config.agent_types import AgentResult, AgentTask

log = logging.getLogger("goat2.agents.coder")

__all__ = ["CoderAgent"]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert software engineer in GOAT 2.0, a multi-agent AI system.

Your role is to write correct, clean, production-quality code. Correctness \
is your first priority. Clarity is your second. Brevity is your third.

Guidelines:
- Use idiomatic patterns for the target language
- Add type annotations (Python: full PEP 484; TypeScript: strict mode)
- Write inline comments only where the logic is non-obvious; never restate what the code does
- Handle error cases explicitly — no silent failures or bare excepts
- Prefer explicit over implicit; avoid magic numbers and global mutable state
- If the task is ambiguous, state your assumptions before the code block

Output format:
1. (optional) A short paragraph stating any assumptions or design decisions
2. One or more fenced code blocks, each tagged with the language:
   ```python
   # code here
   ```
3. (optional) A brief note on usage, limitations, or next steps

Use the `validate_syntax` tool to check Python or JSON code before returning. \
If validation fails, fix the error and revalidate before submitting.\
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CoderAgent(BaseAgent):
    """
    Generates and self-validates code for a given task.

    Default model: deepseek-coder — strong code generation across languages.
    Override: CoderAgent(spec=get_model("gpt-4o"))

    Built-in tools:
      - validate_syntax: check Python or JSON code for errors before returning
    """

    role = "coder"

    def __init__(self, spec: ModelSpec | None = None) -> None:
        from tools import (  # lazy — avoids agent↔tools cycle
            FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
            FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, SHELL,
        )
        _file_tools = [
            FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
            FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, SHELL,
        ]
        super().__init__(
            spec=spec or Settings().agents.get("coder"),
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.2,
            tools=_file_tools,
        )
        log.debug("%s ready spec=%s tools=%s", self.__class__.__name__, self.spec, self.tool_names)

    async def execute(
        self,
        task: AgentTask,
        context: dict[str, AgentResult],
    ) -> str:
        """
        Write code for the given task, using upstream context (research
        findings, plans) as reference material.

        The model may call `validate_syntax` during generation. If it does,
        the corrected code is included in the final response automatically.
        """
        log.debug("%s.execute start task_id=%s prompt_len=%d", self.__class__.__name__, task.id, len(task.prompt))
        messages = self._build_messages(task, context)
        # Tools are enabled: validate_syntax is always available.
        output = await self._chat(messages)
        log.debug("%s.execute done task_id=%s output_len=%d", self.__class__.__name__, task.id, len(output))
        return output

    # ------------------------------------------------------------------
    # Built-in tool: syntax validation
    # ------------------------------------------------------------------

    @tool(
        name="validate_syntax",
        description=(
            "Validate code for syntax errors before submitting. "
            "Returns 'OK' on success or a detailed error message."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Raw source code to validate (no markdown fences)",
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "json"],
                    "description": "Language to validate. Defaults to 'python'.",
                },
            },
            "required": ["code"],
        },
    )
    async def _validate_syntax(
        self,
        code: str,
        language: str = "python",
    ) -> str:
        # Strip markdown fences in case the model accidentally includes them.
        code = re.sub(r"^```[^\n]*\n|```\s*$", "", code.strip(), flags=re.MULTILINE).strip()

        if language == "python":
            try:
                ast.parse(code)
                return "OK — no syntax errors"
            except SyntaxError as exc:
                return (
                    f"SyntaxError on line {exc.lineno}: {exc.msg}\n"
                    f"  {exc.text or ''}"
                ).rstrip()

        if language == "json":
            try:
                json.loads(code)
                return "OK — valid JSON"
            except json.JSONDecodeError as exc:
                return f"JSONDecodeError at line {exc.lineno} col {exc.colno}: {exc.msg}"

        return f"Unsupported language '{language}'. Supported: python, json"
