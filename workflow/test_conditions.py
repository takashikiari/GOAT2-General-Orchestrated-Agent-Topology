"""
Teste pentru conditii in DAG (Varianta B — conditie pe fiecare nod).
"""

import asyncio

from workflow.models import TaskNode, DAGGraph
from workflow.runner import WorkflowRunner


async def test_condition_true():
    """Un nod cu conditie True se executa normal."""
    print("=== Test: conditie True ===")

    async def runner(task_id: str, ctx: dict) -> str:
        return "executat"

    node = TaskNode(
        task_id="main",
        runner=runner,
        condition=lambda ctx: True,
    )
    graph = DAGGraph(nodes=(node,), dag_id="test-cond-true")
    r = WorkflowRunner()
    result = await r.run(graph)

    assert result.success
    assert result.results["main"] == "executat"
    assert "main" not in result.skipped
    assert result.execution_order == ("main",)
    print(f"   ✅ Nod executat: {result.results['main']}")
    print("=== TEST TRECUT ===")


async def test_condition_false():
    """Un nod cu conditie False e skip-at."""
    print("=== Test: conditie False ===")

    async def runner(task_id: str, ctx: dict) -> str:
        return "nu ar trebui sa ajung aici"

    node = TaskNode(
        task_id="main",
        runner=runner,
        condition=lambda ctx: False,
    )
    graph = DAGGraph(nodes=(node,), dag_id="test-cond-false")
    r = WorkflowRunner()
    result = await r.run(graph)

    assert result.success
    assert "main" not in result.results
    assert "main" in result.skipped
    assert result.execution_order == ("main",)
    print(f"   ✅ Nod skip-at: {result.skipped}")
    print("=== TEST TRECUT ===")


async def test_condition_based_on_context():
    """Un nod se executa doar daca un nod anterior a produs un anumit rezultat."""
    print("=== Test: conditie bazata pe context ===")

    async def produce(task_id: str, ctx: dict) -> dict:
        return {"status": "ready", "value": 100}

    async def consume(task_id: str, ctx: dict) -> str:
        data = ctx["producer"]
        return f"consumat: {data['value']}"

    producer = TaskNode(task_id="producer", runner=produce)
    consumer = TaskNode(
        task_id="consumer",
        dependencies=("producer",),
        runner=consume,
        condition=lambda ctx: ctx.get("producer", {}).get("status") == "ready",
    )

    graph = DAGGraph(nodes=(producer, consumer), dag_id="test-cond-context")
    r = WorkflowRunner()
    result = await r.run(graph)

    assert result.success
    assert result.results["producer"]["status"] == "ready"
    assert result.results["consumer"] == "consumat: 100"
    assert "consumer" not in result.skipped
    print(f"   ✅ Nod executat pe baza contextului: {result.results['consumer']}")
    print("=== TEST TRECUT ===")


async def test_condition_skip_chain():
    """Doua noduri consecutive, ambele cu conditie — prima False, a doua True."""
    print("=== Test: skip chain ===")

    async def a_runner(task_id: str, ctx: dict) -> str:
        return "A"

    async def b_runner(task_id: str, ctx: dict) -> str:
        return "B"

    a = TaskNode(
        task_id="node_a",
        runner=a_runner,
        condition=lambda ctx: False,
    )
    b = TaskNode(
        task_id="node_b",
        dependencies=("node_a",),
        runner=b_runner,
        condition=lambda ctx: ctx.get("node_a") == "A",
    )

    graph = DAGGraph(nodes=(a, b), dag_id="test-skip-chain")
    r = WorkflowRunner()
    result = await r.run(graph)

    assert result.success
    assert "node_a" in result.skipped
    assert "node_b" in result.skipped  # n-are ce consuma, conditia nu se indeplineste
    print(f"   ✅ Ambele noduri skip-ate: {result.skipped}")
    print("=== TEST TRECUT ===")


if __name__ == "__main__":
    asyncio.run(test_condition_true())
    asyncio.run(test_condition_false())
    asyncio.run(test_condition_based_on_context())
    asyncio.run(test_condition_skip_chain())
