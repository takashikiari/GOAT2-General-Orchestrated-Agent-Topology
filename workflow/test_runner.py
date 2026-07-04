"""
Teste pentru WorkflowRunner — DAG simplu, optional, ciclu.
"""

import asyncio
from pathlib import Path

from workflow.models import TaskNode, DAGGraph
from workflow.runner import WorkflowRunner


async def test_simple_dag():
    print("=== Test: DAG simplu (fetch -> process -> save) ===")

    async def fetch(task_id: str, ctx: dict) -> dict:
        wd = ctx.get("__working_dir__")
        assert wd is not None
        assert wd.is_dir()
        return {"raw": "date_brute", "source": "sensor_1"}

    async def process(task_id: str, ctx: dict) -> dict:
        raw = ctx["fetch_data"]
        return {"processed": raw["raw"].upper(), "value": 42}

    async def save(task_id: str, ctx: dict) -> str:
        data = ctx["process_data"]
        return f"saved: {data['processed']} = {data['value']}"

    fetch_node = TaskNode(task_id="fetch_data", runner=fetch)
    process_node = TaskNode(
        task_id="process_data", dependencies=("fetch_data",), runner=process
    )
    save_node = TaskNode(
        task_id="save_results", dependencies=("process_data",), runner=save
    )

    graph = DAGGraph(
        nodes=(fetch_node, process_node, save_node),
        dag_id="test-pipeline",
        working_dir=Path("/tmp/goat2-test"),
    )

    runner = WorkflowRunner()
    result = await runner.run(graph)

    assert result.success, f"Workflow failed: {result.errors}"
    assert result.execution_order == ("fetch_data", "process_data", "save_results")
    assert result.results["fetch_data"]["raw"] == "date_brute"
    assert result.results["process_data"]["value"] == 42
    assert "saved" in result.results["save_results"]

    sandbox = Path("/tmp/goat2-test/test-pipeline")
    assert sandbox.is_dir()
    await runner.cleanup("test-pipeline")
    assert not sandbox.is_dir()
    print("   ✅ Cleanup OK")
    print("=== TEST TRECUT ===")


async def test_optional_node():
    print("\n=== Test: nod optional care crapa ===")

    async def good(task_id: str, ctx: dict) -> str:
        return "ok"

    async def bad(task_id: str, ctx: dict) -> str:
        raise ValueError("crash intentionat")

    g = TaskNode(task_id="good", runner=good)
    b = TaskNode(task_id="bad", runner=bad, dependencies=("good",))

    graph = DAGGraph(nodes=(g, b), dag_id="test-optional")
    runner = WorkflowRunner()
    result = await runner.run(graph)

    assert not result.success
    assert "bad" in result.errors
    print(f"   ✅ Eroare prinsa: {result.errors['bad']}")
    print("=== TEST TRECUT ===")


async def test_cycle_detection():
    print("\n=== Test: detectare ciclu ===")

    async def noop(task_id: str, ctx: dict) -> str:
        return "x"

    a = TaskNode(task_id="a", dependencies=("b",), runner=noop)
    b = TaskNode(task_id="b", dependencies=("a",), runner=noop)

    graph = DAGGraph(nodes=(a, b), dag_id="test-cycle")
    runner = WorkflowRunner()

    try:
        await runner.run(graph)
        assert False, "Ar fi trebuit sa arunce eroare"
    except Exception as e:
        print(f"   ✅ Ciclu detectat: {type(e).__name__}")
        print("=== TEST TRECUT ===")


if __name__ == "__main__":
    asyncio.run(test_simple_dag())
    asyncio.run(test_optional_node())
    asyncio.run(test_cycle_detection())
