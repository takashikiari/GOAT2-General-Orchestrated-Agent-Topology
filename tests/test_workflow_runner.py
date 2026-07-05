"""Tests for WorkflowRunner — parallel async DAG execution."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow.errors import CycleDetected
from workflow.models import DAGGraph, TaskNode
from workflow.runner import WorkflowRunner


# ── helpers ───────────────────────────────────────────────────────────────────

async def _echo(task_id: str, ctx: dict) -> str:
    return f"done:{task_id}"


async def _crash(task_id: str, ctx: dict) -> str:
    raise ValueError(f"crash:{task_id}")


def _graph(*nodes: TaskNode, dag_id: str = "test") -> DAGGraph:
    return DAGGraph(nodes=nodes, dag_id=dag_id)


# ── sequential chain ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simple_chain():
    a = TaskNode("a", runner=_echo)
    b = TaskNode("b", dependencies=("a",), runner=_echo)
    c = TaskNode("c", dependencies=("b",), runner=_echo)
    result = await WorkflowRunner().run(_graph(a, b, c))

    assert result.success
    assert result.execution_order == ("a", "b", "c")
    assert result.results["c"] == "done:c"
    assert not result.errors


# ── parallel siblings ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parallel_siblings():
    """B and C have no mutual dependency — both should run concurrently."""
    completed: list[str] = []

    async def tracked(task_id: str, ctx: dict) -> str:
        await asyncio.sleep(0)  # yield to allow interleaving
        completed.append(task_id)
        return task_id

    a = TaskNode("a", runner=tracked)
    b = TaskNode("b", dependencies=("a",), runner=tracked)
    c = TaskNode("c", dependencies=("a",), runner=tracked)
    d = TaskNode("d", dependencies=("b", "c"), runner=tracked)

    result = await WorkflowRunner().run(_graph(a, b, c, d))
    assert result.success
    assert result.execution_order[0] == "a"
    assert set(result.execution_order[1:3]) == {"b", "c"}
    assert result.execution_order[3] == "d"


# ── context propagation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_propagation():
    async def produce(task_id: str, ctx: dict) -> dict:
        return {"value": 42}

    async def consume(task_id: str, ctx: dict) -> int:
        return ctx["producer"]["value"] * 2

    producer = TaskNode("producer", runner=produce)
    consumer = TaskNode("consumer", dependencies=("producer",), runner=consume)

    result = await WorkflowRunner().run(_graph(producer, consumer))
    assert result.success
    assert result.results["consumer"] == 84


# ── condition skip ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_condition_false_skips_node():
    node = TaskNode("main", runner=_echo, condition=lambda ctx: False)
    result = await WorkflowRunner().run(_graph(node))

    assert result.success
    assert "main" in result.skipped
    assert "main" not in result.results


@pytest.mark.asyncio
async def test_condition_true_runs_node():
    node = TaskNode("main", runner=_echo, condition=lambda ctx: True)
    result = await WorkflowRunner().run(_graph(node))

    assert result.success
    assert "main" not in result.skipped
    assert result.results["main"] == "done:main"


@pytest.mark.asyncio
async def test_condition_based_on_upstream():
    async def produce(task_id: str, ctx: dict) -> str:
        return "ready"

    async def consume(task_id: str, ctx: dict) -> str:
        return f"consumed:{ctx['producer']}"

    producer = TaskNode("producer", runner=produce)
    consumer = TaskNode(
        "consumer",
        dependencies=("producer",),
        runner=consume,
        condition=lambda ctx: ctx.get("producer") == "ready",
    )
    result = await WorkflowRunner().run(_graph(producer, consumer))
    assert result.success
    assert result.results["consumer"] == "consumed:ready"


# ── failure handling ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_node_failure_stops_dag():
    a = TaskNode("a", runner=_echo)
    b = TaskNode("b", dependencies=("a",), runner=_crash)
    c = TaskNode("c", dependencies=("b",), runner=_echo)

    result = await WorkflowRunner().run(_graph(a, b, c))
    assert not result.success
    assert "b" in result.errors
    assert isinstance(result.errors["b"], ValueError)


# ── cycle detection ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_raises():
    a = TaskNode("a", dependencies=("b",), runner=_echo)
    b = TaskNode("b", dependencies=("a",), runner=_echo)
    with pytest.raises(CycleDetected):
        await WorkflowRunner().run(_graph(a, b))


# ── sandbox working_dir ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sandbox_created(tmp_path: Path):
    async def check_sandbox(task_id: str, ctx: dict) -> bool:
        wd: Path = ctx["__working_dir__"]
        assert wd.is_dir()
        assert wd.name == task_id
        return True

    node = TaskNode("node_a", runner=check_sandbox)
    graph = DAGGraph(nodes=(node,), dag_id="sandbox-test", working_dir=tmp_path)
    result = await WorkflowRunner().run(graph)
    assert result.success
    assert result.results["node_a"] is True


# ── cleanup ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup(tmp_path: Path):
    runner = WorkflowRunner(working_dir=tmp_path)
    node = TaskNode("x", runner=_echo)
    graph = DAGGraph(nodes=(node,), dag_id="cleanup-test", working_dir=tmp_path)
    await runner.run(graph)

    sandbox = tmp_path / "cleanup-test"
    assert sandbox.is_dir()
    await runner.cleanup("cleanup-test")
    assert not sandbox.exists()


# ── max_concurrent semaphore ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_concurrent_respected():
    """With max_concurrent=1, siblings run one at a time."""
    running: list[int] = [0]
    peak: list[int] = [0]

    async def track(task_id: str, ctx: dict) -> str:
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0)
        running[0] -= 1
        return task_id

    nodes = [TaskNode(f"n{i}", runner=track) for i in range(4)]
    graph = _graph(*nodes)
    await WorkflowRunner(max_concurrent=1).run(graph)
    assert peak[0] == 1
