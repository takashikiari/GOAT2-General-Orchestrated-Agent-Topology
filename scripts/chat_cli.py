"""scripts/chat_cli.py — terminal chat against the REAL Orchestrator + real backends.

Ad-hoc verification tool for the 2026-07-26 cold/drift synchronous-retrieve fix.
Reads one message per line from stdin, feeds it through Orchestrator.run() against
a fixed chat_id, prints the reply plus per-turn diagnostics (turn state, latency,
search stage timing) pulled straight from the real MemoryAnalytics observation.

No tools are exposed (plugin_manager stubbed to an empty tool list) — the model
can only answer from injected context, and nothing (promote_memory, shell_run,
write_file, ...) can fire. Permanent memory (Letta) is never touched.

Usage:
    python3 scripts/chat_cli.py --chat-id test-verify-2026-07-26 <<'EOF'
    message one
    message two
    EOF
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from registry.registry import ServiceRegistry
from orchestrator.orchestrator import Orchestrator


class _NoToolsPluginManager:
    """Stub plugin_manager: zero tools exposed to the LLM this run."""
    tools: list = []


async def main(chat_id: str) -> None:
    registry = ServiceRegistry()
    orch = Orchestrator(
        layers=registry.memory_layers,
        llm_client=registry.llm_client,
        plugin_manager=_NoToolsPluginManager(),
        analytics=registry.memory_analytics,
        tools=[],
    )

    print(f"chat_id = {chat_id!r}  (no tools exposed; Letta/permanent memory untouched)")
    print("=" * 100)

    turn = 0
    for line in sys.stdin:
        msg = line.rstrip("\n")
        if not msg.strip():
            continue
        turn += 1
        print(f"\n--- turn {turn} ---")
        print(f">>> USER: {msg}")
        t0 = time.time()
        reply = await orch.run(msg, chat_id)
        dt = time.time() - t0
        print(f"<<< GOAT ({dt:.3f}s): {reply}")
        print("    (see memory.observability JSON log line above for turn_state/results_found/etc.)")

    print("\n" + "=" * 100)
    print("draining background tasks (archive + prefetch) before exit...")
    await orch.drain_background(timeout=20.0)
    print("done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chat-id", default="test-verify-2026-07-26")
    args = parser.parse_args()
    asyncio.run(main(args.chat_id))
