"""Built-in agent runner implementations for GOAT 2.0.

Each runner is an async callable (AgentTask, dep_results) -> str that sets
task.source before returning so the workflow can propagate source provenance.
Tool selection is now semantic — the LLM autonomously decides when to invoke
tools based on task intent, not regex keyword matching.
"""
from __future__ import annotations
import logging
from config.settings import settings
from supervisor.types import AgentTask, AgentResult
from supervisor.llm_utils import _call_llm, _format_dep_context
from supervisor.tool_runner import _call_with_tools

log = logging.getLogger("goat2.runners")
__all__ = ["_run_researcher", "_run_coder", "_run_critic", "_run_summarizer", "_run_tool_caller"]


async def _run_researcher(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Deep research with forced web_search; raises if web_search was not called.

    The LLM autonomously decides when web search is needed based on task semantics.
    Tool invocation is enforced via tool_choice='required' — source=generated triggers UNVERIFIED.
    """
    from tools import WEB_SEARCH
    context = _format_dep_context(dep_results)
    r = await _call_with_tools(
        settings.agents.get("researcher"),
        [
            {"role": "system", "content": (
                "You are a deep research agent. Synthesize knowledge, trade-offs, and prior art. "
                "Use web_search(query) for current information. Output structured findings."
            )},
            {"role": "user", "content": f"{context}\n\nTask: {task.prompt}".strip()},
        ],
        [WEB_SEARCH],
        tool_choice="required",
    )
    if r.source == "generated":
        raise RuntimeError(
            f"researcher: web_search not invoked (source=generated); "
            f"check model tool-calling capability for '{settings.agents.get('researcher').model_id}'"
        )
    task.source = r.source
    return r.content


async def _run_coder(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Code generation with full FILE_TOOLS access; LLM decides autonomously when to use them.

    The model evaluates task semantics to decide if file operations are needed.
    No regex-based forcing — semantic autonomy enables proper tool selection.
    """
    from tools import FILE_TOOLS
    ctx  = _format_dep_context(dep_results)
    msgs = [
        {"role": "system", "content": (
            "Expert software engineer. "
            "File tools: file_read, file_write, file_create, file_list, file_search, "
            "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
            "Say 'tool not connected' on ERROR. Never ask user to run shell commands. "
            "Write clean typed code in fenced blocks."
        )},
        {"role": "user", "content": f"{ctx}\n\nTask: {task.prompt}".strip()},
    ]
    r = await _call_with_tools(settings.agents.get("coder"), msgs, FILE_TOOLS, temperature=0.2)
    task.source = r.source
    return r.content


async def _run_critic(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Critical review: assessment paragraph + bullet list; source is generated.

    Critic evaluates correctness without tool calls — pure LLM analysis of upstream outputs.
    """
    task.source = "generated"
    context = _format_dep_context(dep_results)
    return await _call_llm(
        settings.agents.get("critic"),
        [
            {"role": "system", "content": (
                "Critical reviewer. Evaluate correctness, completeness, clarity, goal alignment. "
                "Output: one assessment paragraph then a bullet list of issues and suggestions."
            )},
            {"role": "user", "content": f"{context}\n\nReview task: {task.prompt}".strip()},
        ],
    )


async def _run_summarizer(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Synthesis: report only facts from verified upstream outputs; source is generated.

    Skips LLM call entirely when all upstream outputs are empty — prevents hallucination.
    """
    task.source = "generated"
    if dep_results and all(not (r.output or "").strip() for r in dep_results.values()):
        return "Not available. Upstream tasks returned no output."
    context = _format_dep_context(dep_results)
    return await _call_llm(
        settings.agents.get("summarizer"),
        [
            {"role": "system", "content": (
                "Synthesis agent. Report only facts present in the prior agent outputs above. "
                "Do not infer, approximate, or generate content to fill missing information. "
                "If a result is empty or errored, state that it was not retrieved — never invent content. "
                "No filler, apologies, or questions at the end."
            )},
            {"role": "user", "content": f"{context}\n\nSynthesize a final answer for: {task.prompt}".strip()},
        ],
    )


async def _run_tool_caller(task: AgentTask, dep_results: dict[str, AgentResult]) -> str:
    """Tool orchestration with FULL tool access — LLM decides autonomously based on semantic intent.

    Removed needs_internet() regex helper — the model now evaluates task semantics to decide
    when web_search, file operations, or memory queries are needed. This enables proper handling
    of conversational requests like 'Goat! Citește changelogs...' which require file_read access.

    All tools available: FILE_TOOLS + MEMORY_TOOLS + WEB_SEARCH
    tool_choice='auto' allows the model to select tools based on true intent.
    """
    from tools import FILE_TOOLS, WEB_SEARCH, MEMORY_TOOLS
    spec = settings.agents.get("tool_caller")
    if not spec.tool_calling:
        raise RuntimeError(f"tool_caller model '{spec.model_id}' has tool_calling=False; use deepseek-chat/gpt-4o-mini.")
    ctx  = _format_dep_context(dep_results)
    msgs = [
        {"role": "system", "content": (
            "Tool orchestration agent. "
            "File tools: file_read, file_write, file_create, file_list, file_search, "
            "file_grep(path, pattern), file_info(path), file_read_lines(path, start_line, end_line). "
            "Search: web_search. "
            "Memory: memory_search, memory_get, memory_store. "
            "Say 'tool not connected' on ERROR. Never ask user to run shell commands. "
            "Evaluate task semantics to decide which tools are needed — do not wait for explicit commands.")},
        {"role": "user", "content": f"{ctx}\n\nTask: {task.prompt}".strip()},
    ]
    _tools = FILE_TOOLS + MEMORY_TOOLS + [WEB_SEARCH]
    log.debug("tool_caller: tools=%s (semantic selection)", [t.name for t in _tools])
    r = await _call_with_tools(spec, msgs, _tools, tool_choice="auto")
    task.source = r.source
    return r.content
