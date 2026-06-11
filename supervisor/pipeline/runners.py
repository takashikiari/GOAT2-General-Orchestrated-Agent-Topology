"""Built-in agent runner implementations for GOAT 2.0.

Each runner is an async callable (AgentTask, dep_results, registry) -> str
that sets task.source before returning so the workflow can propagate source
provenance. Tool selection is semantic — the LLM autonomously decides when
to invoke tools based on task intent.

SEVERITY OUTPUT (_run_critic):
_run_critic() returns a string starting with
``SEVERITY: PASS|MINOR|MAJOR|CRITICAL`` so WorkflowGraph can decide to
re-execute upstream tasks on critical verdicts.

REGISTRY INJECTION (PHASE 4):
All runner functions require the `registry` parameter and use
``registry.settings.agents.get(role)``.
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from supervisor.types import AgentTask, AgentResult
from utils.llm_utils import _format_dep_context
from tools.tool_runner import _call_with_tools

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.supervisor.pipeline")
__all__ = [
    "_run_researcher",
    "_run_coder",
    "_run_critic",
    "_run_summarizer",
    "_run_tool_caller",
    "_run_memory",
]


async def _run_researcher(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Deep research: web_search + memory_search (working tier only).

    Tool invocation enforced via tool_choice='required'.
    source=generated triggers UNVERIFIED. Uses agents.get("researcher").
    """
    from tools import WEB_SEARCH, MEMORY_SEARCH_DAG
    _settings = registry.settings
    context = _format_dep_context(dep_results)
    r = await _call_with_tools(
        _settings.agents.get("researcher"),
        [
            {"role": "system", "content": (
                "You are a deep research agent. Synthesize knowledge, trade-offs, and prior art. "
                "Use web_search(query) for current information. "
                "Use memory_search(query) to check working memory for prior context. "
                "Output structured findings."
            )},
            {"role": "user", "content": f"{context}\n\nTask: {task.prompt}".strip()},
        ],
        [WEB_SEARCH, MEMORY_SEARCH_DAG],
        tool_choice="required",
    )
    if r.source == "generated":
        raise RuntimeError(
            f"researcher: no tool invoked (source=generated); "
            f"check model tool-calling capability for '{_settings.agents.get('researcher').model_id}'"
        )
    task.source = r.source
    return r.content


async def _run_coder(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Code generation: file tools (8) + shell (read-only). No web_search.

    Uses agents.get("coder")."""
    from tools import (
        FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
        FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, SHELL,
    )
    _settings = registry.settings
    ctx  = _format_dep_context(dep_results)
    _coder_tools = [
        FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
        FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES, SHELL,
    ]
    msgs = [
        {"role": "system", "content": (
            "Expert software engineer. "
            "File tools: file_read, file_write, file_create, file_list, file_search, "
            "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
            "Shell (read-only): ls, pwd, cat, head, tail, grep, find, wc, du. "
            "Say 'tool not connected' on ERROR. Write clean typed code in fenced blocks."
        )},
        {"role": "user", "content": f"{ctx}\n\nTask: {task.prompt}".strip()},
    ]
    r = await _call_with_tools(_settings.agents.get("coder"), msgs, _coder_tools, temperature=0.2)
    task.source = r.source
    return r.content


async def _run_critic(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Critical review: severity verdict + assessment. Read-only memory access.

    Tools: memory_recent, memory_get (working tier, read-only). tool_choice='auto'.
    Output MUST start with SEVERITY: PASS/MINOR/MAJOR/CRITICAL for fallback.
    Uses agents.get("critic")."""
    from tools import MEMORY_RECENT_DAG, MEMORY_GET_DAG
    _settings = registry.settings
    context = _format_dep_context(dep_results)
    r = await _call_with_tools(
        _settings.agents.get("critic"),
        [
            {"role": "system", "content": (
                "Critical reviewer. Evaluate correctness, completeness, clarity, goal alignment. "
                "Use memory_recent/memory_get to check working context if needed (read-only). "
                "Output MUST start with one of these severity lines on its own:\n"
                "SEVERITY: PASS — no critical issues\n"
                "SEVERITY: MINOR — small improvements needed, output usable\n"
                "SEVERITY: MAJOR — significant problems, output should be re-done\n"
                "SEVERITY: CRITICAL — output is wrong, hallucinated, or completely off-target\n\n"
                "Then one assessment paragraph, then a bullet list of issues and suggestions."
            )},
            {"role": "user", "content": f"{context}\n\nReview task: {task.prompt}".strip()},
        ],
        [MEMORY_RECENT_DAG, MEMORY_GET_DAG],
        tool_choice="auto",
    )
    task.source = r.source
    return r.content


async def _run_summarizer(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Synthesis: report only verified upstream facts. Read-only memory_recent access.

    Tools: memory_recent (working tier, read-only). tool_choice='auto'.
    Skips LLM call entirely when all upstream outputs are empty.
    Uses agents.get("summarizer")."""
    from tools import MEMORY_RECENT_DAG
    _settings = registry.settings
    if dep_results and all(not (r.output or "").strip() for r in dep_results.values()):
        task.source = "generated"
        return "Not available. Upstream tasks returned no output."
    context = _format_dep_context(dep_results)
    r = await _call_with_tools(
        _settings.agents.get("summarizer"),
        [
            {"role": "system", "content": (
                "Synthesis agent. Report only facts present in the prior agent outputs above. "
                "Use memory_recent to check recent working context if needed (read-only). "
                "Do not infer, approximate, or generate content to fill missing information. "
                "If a result is empty or errored, state that it was not retrieved — never invent content. "
                "No filler, apologies, or questions at the end."
            )},
            {"role": "user", "content": f"{context}\n\nSynthesize a final answer for: {task.prompt}".strip()},
        ],
        [MEMORY_RECENT_DAG],
        tool_choice="auto",
    )
    task.source = r.source
    return r.content


async def _run_tool_caller(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Tool orchestration: file tools (8) + DAG memory tools (working tier only).

    No web_search, no shell. Memory restricted to dag:* namespace (working tier).
    tool_choice='required' enforces tool invocation. Uses agents.get("tool_caller")."""
    from tools import (
        FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
        FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
        MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
    )
    _settings = registry.settings
    spec = _settings.agents.get("tool_caller")
    if not spec.tool_calling:
        raise RuntimeError(f"tool_caller model '{spec.model_id}' has tool_calling=False; use deepseek-chat/gpt-4o-mini.")
    ctx  = _format_dep_context(dep_results)
    _tools = [
        FILE_READ, FILE_WRITE, FILE_CREATE, FILE_LIST,
        FILE_SEARCH, FILE_GREP, FILE_INFO, FILE_READ_LINES,
        MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
    ]
    msgs = [
        {"role": "system", "content": (
            "Tool orchestration agent. "
            "File tools: file_read, file_write, file_create, file_list, file_search, "
            "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
            "Memory (working tier only, dag:* namespace): "
            "memory_search, memory_get, memory_store, memory_recent. "
            "Say 'tool not connected' on ERROR. "
            "Evaluate task semantics to decide which tools are needed — do not wait for explicit commands.")},
        {"role": "user", "content": f"{ctx}\n\nTask: {task.prompt}".strip()},
    ]
    log.debug("tool_caller: tools=%s", [t.name for t in _tools])
    r = await _call_with_tools(spec, msgs, _tools, tool_choice="required")
    task.source = r.source
    return r.content


async def _run_memory(
    task: AgentTask,
    dep_results: dict[str, AgentResult],
    registry: "Registry",
) -> str:
    """Working-memory persistence: 4 DAG memory tools (working tier only).

    Restricted to dag:* namespace — no ChromaDB, no Letta. tool_choice='required'.
    Reuses the ``tool_caller`` model spec (no dedicated memory model)."""
    from tools import (
        MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG,
    )
    _settings = registry.settings
    spec = _settings.agents.get("tool_caller")
    if not spec.tool_calling:
        raise RuntimeError(
            f"memory model '{spec.model_id}' has tool_calling=False; "
            "memory reuses the tool_caller spec — use deepseek-chat/gpt-4o-mini."
        )
    _tools = [MEMORY_RECENT_DAG, MEMORY_GET_DAG, MEMORY_STORE_DAG, MEMORY_SEARCH_DAG]
    ctx = _format_dep_context(dep_results)
    msgs = [
        {"role": "system", "content": (
            "Working memory agent. Persist and retrieve DAG execution context. "
            "Tools (working tier only, dag:* namespace): "
            "memory_recent, memory_get, memory_store, memory_search. "
            "Do not access ChromaDB or Letta. Report exactly what was stored."
        )},
        {"role": "user", "content": f"{ctx}\n\nTask: {task.prompt}".strip()},
    ]
    log.debug("memory: tools=%s", [t.name for t in _tools])
    r = await _call_with_tools(spec, msgs, _tools, tool_choice="required")
    task.source = r.source
    return r.content
