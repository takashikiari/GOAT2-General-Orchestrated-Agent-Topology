"""scripts.enriching_check — prove the enriching-write refresh ordering invariant.

The plan's manual two-turn check needs an LLM ``store_memory`` tool call, which
the recall benchmarks don't trigger. This script exercises the same code path
(``Orchestrator._enriching_refresh``) deterministically against REAL Redis +
ChromaDB, with no LLM cost: store an on-thread fact to L3, then run the refresh
against a real activation and confirm it re-searches the thread's last_query
and folds the just-written fact into the activation in place — so the *next*
turn would see it. That is the ordering invariant the brain metaphor rests on.

Run: ``source .env && python3 -m scripts.enriching_check``
"""
from __future__ import annotations

import asyncio
import time
import uuid

from memory.activation import Activation
from memory.config import PREFETCH_MAX_RESULTS


async def main() -> None:
    from registry.registry import ServiceRegistry
    from orchestrator.orchestrator import Orchestrator

    reg = ServiceRegistry()
    layers = reg.memory_layers
    orch = Orchestrator(reg)
    chat_id = f"enrich-{uuid.uuid4().hex[:12]}"
    last_query = "what is my wifi password"
    new_fact = "My home wifi password is coffee-mug-42"

    # 1. Build a real cold-turn activation around the thread query (no L3 yet).
    qemb = await layers.embed_query(last_query)
    assert qemb is not None, "embed_query returned None — ChromaDB embedding unavailable"
    activation = Activation(
        centroid=qemb, merged=[], last_query=last_query,
        recent_queries=[last_query], ts=time.time(),
    )
    await layers.set_activation(chat_id, activation)
    print(f"built activation: centroid_dim={len(qemb)} last_query={last_query!r}")

    # 2. Simulate the tool round's store_memory: write the on-thread fact to L3.
    #    (In a real turn, store_memory does this via store_episodic, then the
    #    tool round returns and _enriching_refresh runs — exactly this ordering.)
    await layers.store_episodic(chat_id, new_fact, {"tags": "wifi", "timestamp": time.time()})
    print(f"wrote to L3: {new_fact!r}")

    # 3. The enriching refresh: classify the write against the thread centroid,
    #    re-search last_query uncached, fold the fresh results into the activation.
    kind, refreshed = await orch._enriching_refresh(layers, chat_id, [new_fact], activation)
    print(f"refresh result: write_kind={kind!r} refreshed={refreshed}")
    assert kind == "enriching" and refreshed, (
        f"expected enriching+True, got {kind!r}+{refreshed} — write was misclassified")

    # 4. The activation now holds the just-written fact (the next turn sees it).
    held = await layers.get_activation(chat_id)
    assert held is not None, "activation vanished after refresh"
    found = [r for r in held.merged if "coffee-mug-42" in r.get("content", "")]
    print(f"activation.merged now has {len(held.merged)} results; "
          f"matching the new fact: {len(found)}")
    assert found, "the just-written fact was NOT folded into the activation — invariant broken"
    print("\nPASS: enriching write refreshed the activation in place; the next turn sees the new fact.")


if __name__ == "__main__":
    asyncio.run(main())