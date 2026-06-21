# GOAT 2.0

## Philosophy

Build bottom-up, one module at a time, tested live before adding the next layer.
No speculative abstractions — every line of code earns its place by being
exercised at the time it's written.

## Current architecture (Step 6)

```
config/settings.py          ← reads env vars once; nothing else reads os.environ
    ↓
registry/registry.py        ← ServiceRegistry: constructs + caches the LLM client
    ↓
orchestrator/orchestrator.py ← Orchestrator: intent → LLM → text (one call, no loop)
```

Supporting infrastructure:

```
utils/logging/setup.py      ← get_logger(), register_symbols() (conflict detection)
agents/                     ← existing agent implementations (not yet wired in)
mcp_server/                 ← existing MCP server (not yet wired in)
```

## How to run the minimal end-to-end test

```bash
cd /home/lenovo/workspace/goat2

# Supply your API key (DeepSeek-compatible endpoint by default)
export API_KEY=<your-deepseek-api-key>

python3 -c "
import asyncio
from config import settings
from registry.registry import ServiceRegistry
from orchestrator.orchestrator import Orchestrator

registry = ServiceRegistry()
o = Orchestrator(registry)
result = asyncio.run(o.run('What is 2+2?'))
print(result)
"
```

## Verify individual imports

```bash
python3 -c "from config import settings; print('config ok')"
python3 -c "from registry.registry import ServiceRegistry; print('registry ok')"
python3 -c "from orchestrator.orchestrator import Orchestrator; print('orchestrator ok')"
python3 -c "from utils.logging.setup import get_logger; print('logging ok')"
```

## What is intentionally NOT built yet

| Capability | Reason deferred |
|---|---|
| Conversation memory | Needs a storage layer decision first |
| Tool calling | Orchestrator is one-call only by design at this stage |
| Agent DAG / supervisor | Agents exist but are not wired to the orchestrator |
| Temporal layers | Built on top of memory — deferred with memory |
| Multi-turn loop | No state yet; single turn proves the stack works |
| MCP server wiring | mcp_server/ exists but is not started by the orchestrator |

## Architectural rules (apply to every future module)

1. `TYPE_CHECKING` for all cross-module type imports — avoids circular imports
2. Lazy imports for cross-module instantiation where needed
3. Zero singletons — every stateful object is explicitly constructed and passed (DI)
4. Full docstrings on every class and public function
5. No hardcoded values — everything configurable lives in `config/settings.py`
6. No regex unless absolutely necessary and documented
7. Max 90 lines per file, single responsibility — split before writing
