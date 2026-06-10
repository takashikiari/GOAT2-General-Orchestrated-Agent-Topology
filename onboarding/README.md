# Onboarding System — GOAT 2.0

## Overview

The GOAT 2.0 Onboarding System is a production-ready, DAG-driven setup pipeline that detects environment, configures dependencies, verifies components, and persists identity profiles across all three memory tiers.

## Purpose

The onboarding system ensures:
- **Environment Detection**: OS, Python version, available tools (Redis, ChromaDB, Letta)
- **Dependency Management**: Installs required packages from requirements.txt
- **Configuration Validation**: Validates goat.toml structure and API keys
- **Memory Setup**: Initializes all three memory tiers (working, episodic, long_term)
- **Component Verification**: Validates tools, agents, DAG, and supervisor imports
- **Identity Persistence**: Stores identity profile across memory tiers

## Directory Structure

```
onboarding/
├── __init__.py         # Package marker
├── detect.py           # Phase 1: Environment detection
├── configure.py       # Phase 2: Dependency installation and config validation
├── verify.py         # Phase 3: Component verification
├── persist.py        # Phase 4: Identity persistence
├── orchestrator.py    # Main orchestrator (runs all 4 phases)
└── README.md        # This file
```

## Files

### detect.py — Phase 1: Environment Detection

Detects the current environment and returns a comprehensive snapshot:
- OS, version, architecture
- Python version and executable
- Docker/CI detection
- Available tools: git, Redis, ChromaDB, Letta, SearXNG
- Configuration file existence

**Key Functions:**
- `detect_environment()`: Returns full environment snapshot

### configure.py — Phase 2: Configuration

Installs dependencies and validates configuration:
- Installs requirements.txt and requirements-minimal.txt
- Validates goat.toml structure
- Verifies API keys (env or config)
- Initializes memory tiers

**Key Functions:**
- `install_requirements(env)`: Installs pip packages
- `validate_goat_config()`: Validates goat.toml
- `setup_memory_tiers(env)`: Initializes memory backends

### verify.py — Phase 3: Verification

Verifies all system components:
- Imports and tests all registered tools
- Verifies DAG agent imports
- Tests DAG engine and workflow
- Validates supervisor components
- Runs end-to-end check

**Key Functions:**
- `verify_tools()`: Validates all tool imports
- `verify_agents()`: Verifies DAG agents
- `verify_dag_execution()`: Tests DAG engine
- `verify_supervisor()`: Validates supervisor
- `run_end_to_end_check()`: Lightweight E2E test

### persist.py — Phase 4: Persistence

Stores identity profile across all memory tiers:
- Working (Redis): 24h TTL
- Episodic (ChromaDB): Persistent collection
- Long-term (Letta): Core memory blocks
- File: memory/goat_profile.json

**Key Functions:**
- `persist_identity(env, config_status, memory_status)`: Stores full profile
- `persist_session_profile(profile)`: Stores session profile (1h TTL)

### orchestrator.py — Main Orchestrator

Runs all 4 phases in sequence:
1. Detect → environment snapshot
2. Configure → deps, config, memory
3. Verify → tools, agents, DAG, supervisor, E2E
4. Persist → profile across tiers

**Usage:**
```python
from onboarding.orchestrator import run_onboarding, print_report

result = run_onboarding(skip_install=False, verbose=True)
print_report(result)
```

**CLI:**
```bash
python onboarding/orchestrator.py --skip-install --quiet
```

## Onboarding Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    ONBOARDING FLOW                     │
└─────────────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    ┌─────────┐       ┌──────────┐      ┌──────────┐
    │ DETECT  │ ────▶ │CONFIGURE │ ────▶ │ VERIFY  │
    └─────────┘       └──────────┘      └──────────┘
         │                 │                 │
         ▼                 ▼                 ▼
   Environment      Dependencies      Components
   snapshot        + Config          + Tools
                   + Memory         + Agents
                                   + DAG
                                   + E2E
                           │
                           ▼
                    ┌──────────────┐
                    │   PERSIST   │
                    └──────────────┘
                           │
                           ▼
              Identity profile across all 3 tiers
              (working, episodic, long_term)
```

## Behavioral Learning Integration

The onboarding system integrates with behavioral learning through the identity profile:

1. **Environment Learning**: Captures OS, Python version, available tools
2. **Capability Detection**: Identifies which memory tiers are available
3. **Configuration Tracking**: Stores valid config sections for future reference
4. **Session Profiles**: Stores session-level profiles in working memory

The persisted identity profile can be used by:
- Supervisor to adapt behavior based on environment
- DAG agents to adjust tool selection
- Memory routing to prefer available tiers

## Import Examples

### Run Full Onboarding
```python
from onboarding.orchestrator import run_onboarding, print_report

result = run_onboarding()
print_report(result)
```

### Run Individual Phases
```python
from onboarding.detect import detect_environment
from onboarding.configure import validate_goat_config, setup_memory_tiers
from onboarding.verify import verify_tools, verify_agents
from onboarding.persist import persist_identity

# Phase 1
env = detect_environment()

# Phase 2
config_status = validate_goat_config()
memory_status = setup_memory_tiers(env)

# Phase 3
tools = verify_tools()
agents = verify_agents()

# Phase 4
persist = persist_identity(env, config_status, memory_status)
```

### CLI Usage
```bash
# Full onboarding
python onboarding/orchestrator.py

# Skip pip install (already done)
python onboarding/orchestrator.py --skip-install

# Quiet mode
python onboarding/orchestrator.py --quiet
```

## Configuration

Onboarding constants are in `config/onboarding.py`:

```python
from config.onboarding import (
    GOAT_VERSION,           # "2.0"
    PROFILE_TTL_WORKING,     # 86400 (24h)
    PROFILE_TTL_SESSION,    # 3600 (1h)
    CHROMA_COLLECTION_NAME, # "goat_onboarding"
    REDIS_KEY_PREFIX,     # "goat"
    REDIS_KEY_IDENTITY,   # "goat:identity:profile"
    CONFIG_REQUIRED_SECTIONS, # ["model", "agents", "memory", "supervisor"]
)
```

## Return Values

### run_onboarding() Result

```python
{
    "status": "success" | "partial" | "failed",
    "phases": {
        "detect": {"status": "ok", "os": "...", ...},
        "configure": {"status": "ok", "config_valid": True, ...},
        "memory": {"status": "ok", "working": True, ...},
        "verify": {"status": "ok", "tools": {...}, ...},
        "persist": {"status": "ok", "working": True, ...},
    },
    "summary": {
        "phases_completed": 4,
        "phases_ok": 4,
        "phases_warning": 0,
        "phases_error": 0,
        "total_errors": 0,
    },
    "duration_seconds": 2.5,
    "errors": [],
}
```

## Exit Codes

- `0`: Success (all phases OK)
- `1`: Failed or partial

## Dependencies

- **Python**: 3.11+
- **System**: git, Redis (optional), ChromaDB (optional), Letta (optional)
- **Config**: config/goat.toml with required sections
- **API Keys**: Set via environment or config/goat.toml