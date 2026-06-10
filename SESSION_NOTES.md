# GOAT Session Notes

## Current Architecture

### Reorganization (June 2026)

**Module Structure:**
- `agents/prompts/` — New subdirectory for agent prompt templates
- `agents/researcher_prompt.py` → moved to `agents/prompts/researcher_prompt.py`
- `config/agents.py` — New central registry for agent roles

**Role Constants (config/agents.py):**
- AGENT_ROLES: ["researcher", "coder", "critic", "planner", "summarizer", "tool_caller", "memory"]
- EXECUTION_ROLES: frozenset({"researcher", "tool_caller", "memory"})
- SYNTHESIS_ROLES: frozenset({"summarizer", "critic", "planner"})
- DEFAULT_AGENT_ROLE: "tool_caller"

**Files Updated:**
- supervisor/dag_validator.py — now imports from config/agents.py
- agents/__init__.py — re-exports from prompts/

---

### Memory Access Control (as of June 2026)

**GOAT (Supervisor):**
- Full access to all 3 tiers: Working (Redis), Episodic (ChromaDB), Long-term (Letta)
- Singurul care scrie în Letta
- Gatekeeper pentru informațiile din straturile profunde

**DAG Agents (planner, researcher, coder, critic, summarizer, tool_caller):**
- Acces doar la Working (Redis)
- Nu au acces direct la ChromaDB sau Letta

**Memory Agent:**
- Folosește Redis ca bridge de comunicare
- Își ia context din working memory
- Nu are acces direct la Episodic sau Long-term
- Dacă are nevoie de informații din straturile profunde → face query către GOAT
- GOAT filtrează și decide ce să returneze
- Zero halucinații — nu primește date nefiltrate

### Key Rules

1. Memory agent scrie doar în Redis (working)
2. GOAT promovează din Working → Episodic → Long-term
3. DAG agents nu au tier parameter în memory tools
4. Letta este read-only pentru DAG, write-only pentru GOAT
5. Orice query către straturile profunde trece prin GOAT

### Letta Status

- Letta a fost indisponibilă (picată) până recent
- Acum e funcțională
- Scrierea în Letta e restrictionată doar la GOAT
- DAG agents nu mai au acces să scrie în Letta
- Promovările în Letta funcționează normal acum

---

## Past Sessions Summary

[To be populated as sessions are completed]
