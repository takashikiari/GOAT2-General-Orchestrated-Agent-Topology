"""Critique and synthesis agents — review task results and produce final answer.

FALLBACK ON CRITICAL/MAJOR:
===========================
critique_results() now returns a CriticVerdict dataclass with severity + structured issues.
supervisor.py checks severity and re-executes failing tasks with a stricter prompt
when severity is CRITICAL or MAJOR.

IMPROVEMENTS (FIX):
===================
- parse_verdict() now searches for SEVERITY: anywhere in the text (not just start of line)
- Issues extraction handles multiple formats: -, *, •, numbered lists (1., 2.)
- critique_results() has a timeout to prevent hanging
- Fallback verdict if parsing fails completely

REGISTRY INJECTION (PHASE 4):
=============================
critique_results() and synthesize_results() now require `registry` parameter.
Uses registry.settings.agents.get("critic"/"planner") for LLM calls.
"""
from __future__ import annotations
import asyncio
import re
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config.timeouts import TURN_TIMEOUT
from config.agent_types import AgentResult
from utils.llm_utils import _call_llm, _format_dep_context

if TYPE_CHECKING:
    from config.registry import Registry

log = logging.getLogger("goat2.critique")

__all__ = ["critique_results", "synthesize_results", "CriticVerdict", "parse_verdict"]


@dataclass
class CriticVerdict:
    """Structured verdict from the critic agent.

    severity: PASS | MINOR | MAJOR | CRITICAL
    assessment: One-paragraph assessment
    issues: List of specific issues found
    raw: Full raw output from critic
    """
    severity: str = "PASS"  # PASS, MINOR, MAJOR, CRITICAL
    assessment: str = ""
    issues: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def needs_rerun(self) -> bool:
        """True when the verdict indicates the output should be re-done."""
        return self.severity in ("MAJOR", "CRITICAL")

    @property
    def is_pass(self) -> bool:
        return self.severity == "PASS"


# More flexible severity pattern — matches SEVERITY: anywhere in text
_SEVERITY_RE = re.compile(
    r'SEVERITY\s*:\s*(PASS|MINOR|MAJOR|CRITICAL)',
    re.IGNORECASE,
)

# Issue line patterns: -, *, •, numbered (1., 2.), or any line after "Issues:" header
_ISSUE_LINE_RE = re.compile(r'^[\s]*[-*•]+\s+(.+)$', re.MULTILINE)
_NUMBERED_ISSUE_RE = re.compile(r'^\s*\d+[.)]\s+(.+)$', re.MULTILINE)


def parse_verdict(raw: str) -> CriticVerdict:
    """Parse the critic output into a structured CriticVerdict.

    Looks for 'SEVERITY: <level>' anywhere in the text (case-insensitive).
    Falls back to PASS if no severity line found.

    Extracts issues from bullet points (-, *, •) and numbered lists (1., 2.)).
    The first paragraph before any issues is treated as the assessment.
    """
    if not raw or not raw.strip():
        return CriticVerdict(
            severity="PASS",
            assessment="",
            issues=[],
            raw=raw or "",
        )

    m = _SEVERITY_RE.search(raw)
    severity = m.group(1).upper() if m else "PASS"

    # Split into lines for processing
    lines = raw.split("\n")

    # Find where issues start — look for first bullet or "Issues:" header
    issues_start = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* ") or stripped.startswith("•"):
            issues_start = i
            break
        if stripped.lower().startswith("issues") and ":" in stripped:
            issues_start = i + 1
            break
        # Numbered list
        if re.match(r'^\d+[.)]\s', stripped):
            issues_start = i
            break

    # Extract assessment (everything before issues)
    assessment_lines = lines[:issues_start]
    # Remove SEVERITY line from assessment
    assessment_lines = [
        l for l in assessment_lines
        if not _SEVERITY_RE.search(l)
    ]
    assessment = "\n".join(assessment_lines).strip()

    # Extract issues using regex patterns
    issues: list[str] = []

    # Try bullet patterns
    for match in _ISSUE_LINE_RE.finditer(raw):
        issue = match.group(1).strip()
        if issue and issue not in issues:
            issues.append(issue)

    # Try numbered patterns
    for match in _NUMBERED_ISSUE_RE.finditer(raw):
        issue = match.group(1).strip()
        if issue and issue not in issues:
            issues.append(issue)

    # Fallback: if no issues found via regex, try heuristic from lines after assessment
    if not issues and issues_start < len(lines):
        for line in lines[issues_start:]:
            stripped = line.strip()
            if stripped and not stripped.lower().startswith("issues"):
                # Skip empty lines and SEVERITY lines
                if not _SEVERITY_RE.search(stripped):
                    issues.append(stripped)

    return CriticVerdict(
        severity=severity,
        assessment=assessment,
        issues=issues,
        raw=raw,
    )


async def critique_results(
    intent: str,
    results: dict[str, AgentResult],
    registry: "Registry",
    lang: str = "",
) -> CriticVerdict:
    """Critic agent reviews the full set of completed task results end-to-end.

    Returns a CriticVerdict with severity classification for fallback logic.
    Has a timeout to prevent hanging on LLM failures.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get("critic").
    """
    _settings = registry.settings
    context = _format_dep_context(results)
    lang_pfx = f"Respond in {lang}. " if lang and lang.lower() != "english" else ""
    try:
        raw = await asyncio.wait_for(
            _call_llm(
                _settings.agents.get("critic"),
                [
                    {"role": "system", "content": (
                        f"{lang_pfx}You are a critical reviewer for a multi-agent pipeline. "
                        "Evaluate all agent outputs for correctness, completeness, and alignment "
                        "with the original intent.\n\n"
                        "Your response MUST include a severity line:\n"
                        "SEVERITY: PASS — all good, no critical issues\n"
                        "SEVERITY: MINOR — small improvements, output usable\n"
                        "SEVERITY: MAJOR — significant problems, output should be re-done\n"
                        "SEVERITY: CRITICAL — output is wrong, hallucinated, or completely off-target\n\n"
                        "Then one concise assessment paragraph, then a bullet list of issues and suggestions."
                    )},
                    {"role": "user", "content": (
                        f"Original intent: {intent}\n\n{context}\n\nProvide a critical review."
                    )},
                ],
            ),
            timeout=TURN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.error("critique_results timed out after %ds", TURN_TIMEOUT)
        return CriticVerdict(
            severity="PASS",
            assessment="Critique timed out — no review available.",
            issues=["Critique agent timed out"],
            raw="",
        )
    except Exception as exc:
        log.error("critique_results failed: %s: %s", type(exc).__name__, exc)
        return CriticVerdict(
            severity="PASS",
            assessment=f"Critique failed: {exc}",
            issues=[f"Critique error: {exc}"],
            raw="",
        )
    return parse_verdict(raw)


async def synthesize_results(
    intent: str,
    results: dict[str, AgentResult],
    critique: str,
    registry: "Registry",
    profile: str = "",
    style: str = "",
    lang: str = "",
    session_summary: str = "",
    dag_detail: str = "",
) -> str:
    """Synthesize agent outputs into a terse, persona-matched final answer.

    When dag_detail is provided, prepends [DAG Execution Result] to context
    to ensure the LLM synthesizes from real DAG output instead of hallucinating.

    REGISTRY INJECTION (PHASE 4):
    =============================
    Requires registry parameter. Uses registry.settings.agents.get("planner").
    """
    from supervisor.identity import _system_with_profile  # lazy — breaks agents ↔ supervisor cycle
    _settings = registry.settings
    context = _format_dep_context(results)
    sys_base = _system_with_profile(profile, session_summary, style)
    lang_sfx = f"\nRespond in {lang}." if lang and lang.lower() != "english" else ""

    # Prepend DAG execution result to context when available
    if dag_detail:
        context = f"[DAG Execution Result]\n{dag_detail}\n\n{context}"

    return await _call_llm(
        _settings.agents.get("planner"),
        [
            {"role": "system", "content": (
                f"{sys_base}{lang_sfx}\n\nDeliver a direct answer that mirrors the user's tone. "
                "State only facts present in the agent outputs above — do not infer or approximate. "
                "If information is absent or errored, state it was not found — do not generate content. "
                "No headers, no tables, no preamble labels. No apologies. No questions at the end."
            )},
            {"role": "user", "content": (
                f"Original intent: {intent}\n\n{context}\n\n"
                f"Critique notes: {critique}"
            )},
        ],
        temperature=0.5,
    )
