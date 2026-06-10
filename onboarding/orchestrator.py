"""GOAT 🐐 Onboarding Orchestrator — DAG-driven, production-ready setup.

Runs 4 phases as DAG tasks:
  1. detect   → environment snapshot
  2. configure → install deps, validate config, setup memory
  3. verify   → validate tools, agents, DAG, e2e
  4. persist  → store profile across all memory tiers

Usage:
    from onboarding.orchestrator import run_onboarding
    result = run_onboarding()
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onboarding.detect import detect_environment
from onboarding.configure import install_requirements, validate_goat_config, setup_memory_tiers
from onboarding.verify import (
    verify_tools,
    verify_agents,
    verify_dag_execution,
    verify_supervisor,
    run_end_to_end_check,
)
from onboarding.persist import persist_identity


def run_onboarding(skip_install: bool = False, verbose: bool = True) -> dict:
    """Run full onboarding DAG. Returns structured result."""
    start_time = time.time()
    result = {
        "status": "running",
        "phases": {},
        "summary": {},
        "duration_seconds": 0,
        "errors": [],
    }

    def _log(msg: str):
        if verbose:
            print(f"  🐐 {msg}")

    # ─── Phase 1: Detect ───────────────────────────────────────────────
    _log("Phase 1/4: Detect environment...")
    try:
        env = detect_environment()
        result["phases"]["detect"] = {
            "status": "ok",
            "os": env["os"],
            "python": env["python_version"][:60],
            "has_redis": env["has_redis"],
            "has_chromadb": env["has_chromadb"],
            "has_letta": env["has_letta"],
            "has_searxng": env["has_searxng"],
            "is_docker": env["is_docker"],
            "is_ci": env["is_ci"],
        }
        _log(f"  OS: {env['os']} | Python: {env['python_version'][:40]}")
        _log(f"  Redis: {'✅' if env['has_redis'] else '❌'} | ChromaDB: {'✅' if env['has_chromadb'] else '❌'} | Letta: {'✅' if env['has_letta'] else '❌'}")
    except Exception as e:
        result["phases"]["detect"] = {"status": "error", "error": str(e)}
        result["errors"].append(f"detect: {e}")
        result["status"] = "failed"
        return result

    # ─── Phase 2: Configure ────────────────────────────────────────────
    _log("Phase 2/4: Configure system...")

    # 2a: Install requirements
    if not skip_install:
        try:
            install_result = install_requirements(env)
            if install_result.get("errors"):
                for err in install_result["errors"]:
                    _log(f"  ⚠️  {err}")
            _log(f"  Pip: {'✅' if install_result['pip_installed'] else '⚠️ partial'}")
        except Exception as e:
            result["errors"].append(f"install: {e}")
            _log(f"  ⚠️  Install error: {e}")

    # 2b: Validate config
    try:
        config_status = validate_goat_config()
        result["phases"]["configure"] = {
            "status": "ok" if config_status["valid"] else "warning",
            "config_valid": config_status["valid"],
            "sections": config_status["sections"],
        }
        if config_status["errors"]:
            result["phases"]["configure"]["warnings"] = config_status["errors"]
            for err in config_status["errors"]:
                _log(f"  ⚠️  Config: {err}")
        _log(f"  Config: {'✅' if config_status['valid'] else '⚠️ issues'}")
    except Exception as e:
        result["phases"]["configure"] = {"status": "error", "error": str(e)}
        result["errors"].append(f"configure: {e}")

    # 2c: Setup memory tiers
    try:
        memory_status = setup_memory_tiers(env)
        result["phases"]["memory"] = {
            "status": "ok",
            "working": memory_status["working"],
            "episodic": memory_status["episodic"],
            "long_term": memory_status["long_term"],
        }
        _log(f"  Memory: working={'✅' if memory_status['working'] else '❌'} episodic={'✅' if memory_status['episodic'] else '❌'} long_term={'✅' if memory_status['long_term'] else '❌'}")
    except Exception as e:
        result["phases"]["memory"] = {"status": "error", "error": str(e)}
        result["errors"].append(f"memory setup: {e}")

    # ─── Phase 3: Verify ───────────────────────────────────────────────
    _log("Phase 3/4: Verify system...")

    try:
        tools_status = verify_tools()
        agents_status = verify_agents()
        dag_status = verify_dag_execution()
        sup_status = verify_supervisor()
        e2e_status = run_end_to_end_check()

        result["phases"]["verify"] = {
            "status": "ok",
            "tools": {
                "total": len(tools_status["tools"]),
                "ok": sum(1 for t in tools_status["tools"].values() if t["status"] == "ok"),
                "errors": tools_status["errors"][:3],
            },
            "agents": {
                "total": len(agents_status["agents"]),
                "ok": sum(1 for a in agents_status["agents"].values() if a["status"] == "ok"),
            },
            "dag": {
                "import": dag_status["dag_import"],
                "workflow": dag_status["workflow_import"],
            },
            "supervisor": {
                "import": sup_status["supervisor_import"],
                "components": sup_status["components"],
            },
            "e2e": {"passed": e2e_status["passed"]},
        }
        _log(f"  Tools: {result['phases']['verify']['tools']['ok']}/{result['phases']['verify']['tools']['total']} ✅")
        _log(f"  Agents: {result['phases']['verify']['agents']['ok']}/{result['phases']['verify']['agents']['total']} ✅")
        _log(f"  DAG: {'✅' if dag_status['dag_import'] else '❌'} | Workflow: {'✅' if dag_status['workflow_import'] else '❌'}")
        _log(f"  Supervisor: {'✅' if sup_status['supervisor_import'] else '❌'}")
        _log(f"  E2E: {'✅' if e2e_status['passed'] else '❌'}")
    except Exception as e:
        result["phases"]["verify"] = {"status": "error", "error": str(e)}
        result["errors"].append(f"verify: {e}")

    # ─── Phase 4: Persist ──────────────────────────────────────────────
    _log("Phase 4/4: Persist identity...")
    try:
        persist_result = persist_identity(env, config_status, memory_status)
        result["phases"]["persist"] = {
            "status": "ok",
            "working": persist_result.get("working", False),
            "episodic": persist_result.get("episodic", False),
            "long_term": persist_result.get("long_term", False),
            "file": persist_result.get("file_saved"),
        }
        _log(f"  Profile saved: working={'✅' if persist_result.get('working') else '❌'} episodic={'✅' if persist_result.get('episodic') else '❌'} long_term={'✅' if persist_result.get('long_term') else '❌'}")
        if persist_result.get("file_saved"):
            _log(f"  File: {persist_result['file_saved']}")
    except Exception as e:
        result["phases"]["persist"] = {"status": "error", "error": str(e)}
        result["errors"].append(f"persist: {e}")

    # ─── Summary ───────────────────────────────────────────────────────
    duration = time.time() - start_time
    result["duration_seconds"] = round(duration, 2)

    phase_statuses = [p.get("status", "error") for p in result["phases"].values()]
    if all(s == "ok" for s in phase_statuses):
        result["status"] = "success"
    elif any(s == "error" for s in phase_statuses):
        result["status"] = "failed"
    else:
        result["status"] = "partial"

    result["summary"] = {
        "phases_completed": len(result["phases"]),
        "phases_ok": sum(1 for s in phase_statuses if s == "ok"),
        "phases_warning": sum(1 for s in phase_statuses if s == "warning"),
        "phases_error": sum(1 for s in phase_statuses if s == "error"),
        "total_errors": len(result["errors"]),
        "duration": result["duration_seconds"],
    }

    _log(f"\n  🎯 Onboarding {result['status'].upper()} in {result['duration_seconds']}s")
    if result["errors"]:
        for err in result["errors"]:
            _log(f"  ❌ {err}")

    return result


def print_report(result: dict):
    """Pretty-print onboarding report."""
    status_icon = {"success": "✅", "failed": "❌", "partial": "⚠️", "running": "🔄"}
    icon = status_icon.get(result["status"], "❓")

    print(f"\n{'='*60}")
    print(f"  GOAT 🐐 Onboarding Report")
    print(f"{'='*60}")
    print(f"  Status: {icon} {result['status'].upper()}")
    print(f"  Duration: {result['duration_seconds']}s")
    print()

    for phase_name, phase_data in result.get("phases", {}).items():
        p_icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(phase_data.get("status", "error"), "❓")
        print(f"  {p_icon} {phase_name}: {phase_data.get('status', 'unknown')}")
        for key, val in phase_data.items():
            if key == "status":
                continue
            if isinstance(val, dict):
                print(f"      {key}: {json.dumps(val, default=str)[:120]}")
            elif isinstance(val, list) and val:
                print(f"      {key}: {', '.join(str(v)[:60] for v in val[:3])}")
            elif val:
                print(f"      {key}: {str(val)[:100]}")
        print()

    if result.get("errors"):
        print(f"  ❌ Errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"      • {err}")
        print()

    summary = result.get("summary", {})
    print(f"  📊 Summary: {summary.get('phases_ok', 0)}/{summary.get('phases_completed', 0)} phases OK")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GOAT 🐐 Onboarding System")
    parser.add_argument("--skip-install", action="store_true", help="Skip pip install")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    result = run_onboarding(skip_install=args.skip_install, verbose=not args.quiet)
    if not args.quiet:
        print_report(result)

    sys.exit(0 if result["status"] == "success" else 1)
