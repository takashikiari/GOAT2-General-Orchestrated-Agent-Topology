"""Test critic fallback flow (Problema 5).

Verifică:
1. Critic cu SEVERITY: PASS → nu se declanșează fallback
2. Critic cu SEVERITY: CRITICAL → re-execută upstream + re-run critic
3. Critic cu SEVERITY: MAJOR → re-execută (la fel ca CRITICAL)
4. Critic fără depends_on → skip cu warning
5. Limită de re-executări (_MAX_CRITIC_RERUNS = 1)
6. Timeout pe upstream re-execution → păstrează rezultatul original
7. Timeout pe critic re-run → păstrează rezultatul original al criticului
8. Upstream task eșuează la re-executare → păstrează rezultatul original
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supervisor.workflow import (
    WorkflowGraph,
    _parse_critic_severity,
    _extract_critic_feedback,
    _MAX_CRITIC_RERUNS,
    _UPSTREAM_REEXEC_TIMEOUT,
    _CRITIC_RERUN_TIMEOUT,
)
from supervisor.types import AgentTask, AgentResult


# ── Helpers ──

def make_task(
    task_id: str,
    role: str = "researcher",
    prompt: str = "Research topic X",
    depends_on: list[str] | None = None,
    source: str = "test",
) -> AgentTask:
    return AgentTask(
        id=task_id,
        role=role,
        prompt=prompt,
        depends_on=depends_on or [],
        source=source,
    )


def make_critic_task(
    task_id: str = "c1",
    prompt: str = "Review the output",
    depends_on: list[str] | None = None,
) -> AgentTask:
    return make_task(
        task_id=task_id,
        role="critic",
        prompt=prompt,
        depends_on=depends_on or [],
    )


def make_mock_registry(runner_map: dict[str, AsyncMock]) -> MagicMock:
    """Create a mock AgentRegistry that returns predefined runners."""
    registry = MagicMock()
    registry.get.side_effect = lambda role: runner_map.get(role)
    return registry


# ── Tests for helper functions ──

class TestParseCriticSeverity:
    def test_pass(self):
        output = "SEVERITY: PASS\n\nAll good."
        sev, clean = _parse_critic_severity(output)
        assert sev == "PASS"
        assert "All good." in clean

    def test_minor(self):
        output = "SEVERITY: MINOR\n\nMinor issues."
        sev, clean = _parse_critic_severity(output)
        assert sev == "MINOR"

    def test_major(self):
        output = "SEVERITY: MAJOR\n\nMajor issues found."
        sev, clean = _parse_critic_severity(output)
        assert sev == "MAJOR"

    def test_critical(self):
        output = "SEVERITY: CRITICAL\n\nCritical flaws."
        sev, clean = _parse_critic_severity(output)
        assert sev == "CRITICAL"

    def test_no_severity(self):
        output = "Some random output without severity."
        sev, clean = _parse_critic_severity(output)
        assert sev == "UNKNOWN"

    def test_severity_with_extra_spaces(self):
        output = "SEVERITY:   CRITICAL\n\nIssues."
        sev, clean = _parse_critic_severity(output)
        assert sev == "CRITICAL"

    def test_severity_lowercase(self):
        output = "severity: critical\n\nIssues."
        sev, clean = _parse_critic_severity(output)
        assert sev == "UNKNOWN"


class TestExtractCriticFeedback:
    def test_strips_severity_line(self):
        output = "SEVERITY: CRITICAL\n\nIssue 1: wrong answer\nIssue 2: missing source"
        feedback = _extract_critic_feedback(output)
        assert "SEVERITY:" not in feedback
        assert "Issue 1" in feedback

    def test_preserves_content(self):
        output = "SEVERITY: PASS\n\nAll good."
        feedback = _extract_critic_feedback(output)
        assert feedback == "All good."

    def test_empty_output(self):
        feedback = _extract_critic_feedback("")
        assert feedback == ""


# ── Tests for WorkflowGraph fallback ──

class TestCriticFallback:
    """Test the _re_execute_upstream_and_critic method."""

    @pytest.mark.asyncio
    async def test_pass_does_not_trigger_fallback(self):
        """SEVERITY: PASS → fallback NU se declanșează."""
        researcher = AsyncMock(return_value="Research result v1")
        critic = AsyncMock(return_value="SEVERITY: PASS\n\nAll good.")

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Critic a rulat o singură dată
        assert critic.call_count == 1
        # Researcher a rulat o singură dată
        assert researcher.call_count == 1
        # Rezultatul criticului e PASS
        assert "PASS" in results["c1"].output

    @pytest.mark.asyncio
    async def test_critical_triggers_fallback(self):
        """SEVERITY: CRITICAL → re-execută upstream + re-run critic."""
        # Researcher: first call returns v1, second call (re-exec) returns v2
        researcher = AsyncMock(side_effect=["Research result v1", "Research result v2 (improved)"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nWrong answer, missing sources.",
            "SEVERITY: PASS\n\nNow correct.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Critic a rulat de 2 ori (prima + re-run)
        assert critic.call_count == 2
        # Researcher a rulat de 2 ori (prima + re-exec)
        assert researcher.call_count == 2
        # Rezultatul final al criticului e PASS
        assert "PASS" in results["c1"].output
        # Rezultatul final al researcher-ului e versiunea îmbunătățită
        assert "improved" in results["t1"].output

    @pytest.mark.asyncio
    async def test_major_triggers_fallback(self):
        """SEVERITY: MAJOR → re-execută (la fel ca CRITICAL)."""
        researcher = AsyncMock(side_effect=["v1", "v2 (fixed)"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: MAJOR\n\nMissing details.",
            "SEVERITY: PASS\n\nAll good now.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        assert critic.call_count == 2
        assert researcher.call_count == 2
        assert "PASS" in results["c1"].output

    @pytest.mark.asyncio
    async def test_critic_no_depends_on_skips_fallback(self):
        """Critic fără depends_on → skip cu warning (nu crapă)."""
        researcher = AsyncMock(return_value="Research result")
        critic = AsyncMock(return_value="SEVERITY: CRITICAL\n\nIssues.")

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=[]),  # fără depends_on
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        # Nu trebuie să arunce excepție
        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Critic a rulat o singură dată (fallback-ul n-a făcut nimic)
        assert critic.call_count == 1

    @pytest.mark.asyncio
    async def test_max_reruns_limit(self):
        """Limită de re-executări: maxim _MAX_CRITIC_RERUNS (1) per critic."""
        researcher = AsyncMock(side_effect=["v1", "v2", "v3"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nIssues v1.",
            "SEVERITY: CRITICAL\n\nStill issues v2.",
            "SEVERITY: CRITICAL\n\nStill issues v3.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Critic a rulat de 2 ori (prima + un singur re-run)
        assert critic.call_count == 2
        # Researcher a rulat de 2 ori (prima + un singur re-exec)
        assert researcher.call_count == 2

    @pytest.mark.asyncio
    async def test_upstream_timeout_preserves_original(self):
        """Timeout pe upstream re-execution → păstrează rezultatul original."""
        # Researcher: primul apel rapid, al doilea apel timeout
        async def slow_researcher(task, context):
            await asyncio.sleep(999)  # va face timeout
            return "too late"

        researcher = AsyncMock(side_effect=["Research result v1", slow_researcher])
        # Ajustăm: folosim side_effect cu funcții
        researcher = AsyncMock()
        researcher.side_effect = ["Research result v1", slow_researcher]

        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nIssues.",
            "SEVERITY: PASS\n\nFixed.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})

        with patch("supervisor.workflow._UPSTREAM_REEXEC_TIMEOUT", 0.1):  # timeout rapid
            wg = WorkflowGraph(tasks)
            results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Rezultatul upstream trebuie să fie cel original (v1)
        assert "v1" in results["t1"].output

    @pytest.mark.asyncio
    async def test_critic_rerun_timeout_preserves_original(self):
        """Timeout pe critic re-run → păstrează rezultatul original al criticului."""
        researcher = AsyncMock(side_effect=["v1", "v2"])

        async def slow_critic(task, context):
            await asyncio.sleep(999)
            return "too late"

        critic = AsyncMock()
        critic.side_effect = [
            "SEVERITY: CRITICAL\n\nIssues.",
            slow_critic,
        ]

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})

        with patch("supervisor.workflow._CRITIC_RERUN_TIMEOUT", 0.1):  # timeout rapid
            wg = WorkflowGraph(tasks)
            results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Rezultatul criticului trebuie să fie cel original (primul verdict)
        assert "CRITICAL" in results["c1"].output

    @pytest.mark.asyncio
    async def test_upstream_execution_failure_preserves_original(self):
        """Upstream task eșuează la re-executare → păstrează rezultatul original."""
        researcher = AsyncMock(side_effect=[
            "Research result v1",
            Exception("Crash during re-execution"),
        ])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nIssues.",
            "SEVERITY: PASS\n\nFixed.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt="Research topic X"),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Rezultatul upstream trebuie să fie cel original (v1)
        assert "v1" in results["t1"].output

    @pytest.mark.asyncio
    async def test_prompt_restored_after_re_execution(self):
        """Promptul original e restaurat după re-executare."""
        original_prompt = "Research topic X"
        researcher = AsyncMock(side_effect=["v1", "v2"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nIssues.",
            "SEVERITY: PASS\n\nFixed.",
        ])

        tasks = [
            make_task("t1", role="researcher", prompt=original_prompt),
            make_critic_task("c1", depends_on=["t1"]),
        ]

        registry = make_mock_registry({"researcher": researcher, "critic": critic})
        wg = WorkflowGraph(tasks)

        await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # După execuție, promptul trebuie să fie cel original
        assert tasks[0].prompt == original_prompt

    @pytest.mark.asyncio
    async def test_multiple_upstream_tasks_all_reexecuted(self):
        """Toate upstream task-urile sunt re-executate, nu doar primul."""
        researcher1 = AsyncMock(side_effect=["R1 v1", "R1 v2 (fixed)"])
        researcher2 = AsyncMock(side_effect=["R2 v1", "R2 v2 (fixed)"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nBoth need fixing.",
            "SEVERITY: PASS\n\nBoth fixed.",
        ])

        tasks = [
            make_task("r1", role="researcher", prompt="Research A", depends_on=[]),
            make_task("r2", role="researcher", prompt="Research B", depends_on=[]),
            make_critic_task("c1", depends_on=["r1", "r2"]),
        ]

        registry = make_mock_registry({
            "researcher": AsyncMock(),  # fallback — nu ar trebui folosit
            "critic": critic,
        })
        # Folosim side_effect per-task prin registry.get care returnează diferit
        registry.get.side_effect = lambda role: {
            "researcher": researcher1 if role == "researcher" else researcher2,
            "critic": critic,
        }.get(role)

        # Ajustăm: registry.get trebuie să returneze runnerul corect
        # Vom face un registry mai simplu
        registry = MagicMock()

        def get_runner(role):
            if role == "critic":
                return critic
            return researcher1  # ambii researcheri primesc același mock

        registry.get.side_effect = get_runner

        wg = WorkflowGraph(tasks)

        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Ambii researcheri au fost re-executați
        assert researcher1.call_count == 2
        # Critic a rulat de 2 ori
        assert critic.call_count == 2
        # Ambele rezultate sunt versiunile "fixed"
        assert "fixed" in results["r1"].output
        assert "fixed" in results["r2"].output


# ── Integration test: flow complet cu DAG real ──

class TestCriticFallbackIntegration:
    """Testează flow-ul complet: DAG cu mai multe wave-uri + fallback."""

    @pytest.mark.asyncio
    async def test_two_wave_dag_with_fallback(self):
        """DAG cu 2 wave-uri: wave0 = [r1, r2], wave1 = [c1]."""
        r1 = AsyncMock(side_effect=["R1 result", "R1 improved"])
        r2 = AsyncMock(side_effect=["R2 result", "R2 improved"])
        critic = AsyncMock(side_effect=[
            "SEVERITY: CRITICAL\n\nBoth outputs need work.",
            "SEVERITY: PASS\n\nBoth are good now.",
        ])

        tasks = [
            make_task("r1", role="researcher", prompt="Research A"),
            make_task("r2", role="researcher", prompt="Research B"),
            make_critic_task("c1", depends_on=["r1", "r2"]),
        ]

        registry = MagicMock()
        side_effects = {"r1": r1, "r2": r2, "c1": critic}
        registry.get.side_effect = lambda role: side_effects.get(role)

        wg = WorkflowGraph(tasks)
        results = await wg.execute(registry, asyncio.Semaphore(10), verbose=True)

        # Toate task-urile au rezultate
        assert "r1" in results
        assert "r2" in results
        assert "c1" in results
        # Critic verdict final e PASS
        assert "PASS" in results["c1"].output
        # Ambele rezultate upstream sunt îmbunătățite
        assert "improved" in results["r1"].output
        assert "improved" in results["r2"].output
