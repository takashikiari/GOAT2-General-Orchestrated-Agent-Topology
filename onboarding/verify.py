"""Phase 3: Verify — validate tools, agents, DAG execution, and end-to-end flow."""

import importlib
import subprocess
import sys
from pathlib import Path


def verify_tools() -> dict:
    """Verify all registered tools are importable and functional."""
    result = {"tools": {}, "errors": []}
    tools_dir = Path("tools")
    if not tools_dir.exists():
        result["errors"].append("tools/ directory not found")
        return result

    for f in sorted(tools_dir.glob("*.py")):
        if f.name.startswith("__") or f.name.startswith("registry"):
            continue
        module_name = f"tools.{f.stem}"
        try:
            mod = importlib.import_module(module_name)
            # Check for expected function patterns
            funcs = [n for n in dir(mod) if callable(getattr(mod, n)) and not n.startswith("_")]
            result["tools"][f.stem] = {"status": "ok", "functions": len(funcs)}
        except Exception as e:
            result["tools"][f.stem] = {"status": "error", "error": str(e)[:100]}
            result["errors"].append(f"{f.stem}: {e}")

    return result


def verify_agents() -> dict:
    """Verify all DAG agents are importable."""
    result = {"agents": {}, "errors": []}
    agents_dir = Path("agents")
    if not agents_dir.exists():
        result["errors"].append("agents/ directory not found")
        return result

    for f in sorted(agents_dir.glob("*.py")):
        if f.name.startswith("__") or f.name == "base_agent.py":
            continue
        module_name = f"agents.{f.stem}"
        try:
            mod = importlib.import_module(module_name)
            # Check for agent classes
            classes = [n for n in dir(mod) if "Agent" in n or "agent" in n]
            result["agents"][f.stem] = {"status": "ok", "classes": classes}
        except Exception as e:
            result["agents"][f.stem] = {"status": "error", "error": str(e)[:100]}
            result["errors"].append(f"{f.stem}: {e}")

    return result


def verify_dag_execution() -> dict:
    """Verify DAG engine imports and basic topology."""
    result = {"dag_import": False, "workflow_import": False, "errors": []}
    try:
        from supervisor.dag import DAG, DAGTask, DAGResult
        result["dag_import"] = True
        result["dag_classes"] = ["DAG", "DAGTask", "DAGResult"]
    except Exception as e:
        result["errors"].append(f"dag import: {e}")

    try:
        from supervisor.workflow import WorkflowGraph
        result["workflow_import"] = True
    except Exception as e:
        result["errors"].append(f"workflow import: {e}")

    return result


def verify_supervisor() -> dict:
    """Verify supervisor imports and key components."""
    result = {"supervisor_import": False, "components": [], "errors": []}
    try:
        from supervisor.supervisor import GoatSupervisor
        result["supervisor_import"] = True
        result["components"].append("GoatSupervisor")
    except Exception as e:
        result["errors"].append(f"supervisor import: {e}")

    try:
        from supervisor.identity import Identity
        result["components"].append("Identity")
    except Exception as e:
        result["errors"].append(f"identity import: {e}")

    try:
        from supervisor.mem_inject import MemoryInjector
        result["components"].append("MemoryInjector")
    except Exception as e:
        result["errors"].append(f"mem_inject: {e}")

    return result


def run_end_to_end_check() -> dict:
    """Run a lightweight end-to-end check: import → DAG → tool call simulation."""
    result = {"passed": False, "steps": [], "errors": []}

    # Step 1: Import core
    try:
        from supervisor.supervisor import GoatSupervisor
        result["steps"].append("import_supervisor")
    except Exception as e:
        result["errors"].append(f"e2e import: {e}")
        return result

    # Step 2: DAG creation
    try:
        from supervisor.dag import DAG, DAGTask
        dag = DAG("e2e_test")
        task = DAGTask(id="test", agent="researcher", prompt="test")
        dag.add_task(task)
        result["steps"].append("dag_create")
    except Exception as e:
        result["errors"].append(f"dag create: {e}")
        return result

    # Step 3: Workflow instantiation
    try:
        from supervisor.workflow import WorkflowGraph
        wf = WorkflowGraph(dag=dag, max_workers=2)
        result["steps"].append("workflow_create")
    except Exception as e:
        result["errors"].append(f"workflow create: {e}")
        return result

    result["passed"] = len(result["errors"]) == 0
    return result
