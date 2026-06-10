# GOAT Changelog

## [Unreleased]

### Added
- **config/agents.py**: Central agent role registry with AGENT_ROLES, EXECUTION_ROLES, SYNTHESIS_ROLES, DEFAULT_AGENT_ROLE
- **agents/prompts/**: New subdirectory for prompt templates
  - `agents/prompts/__init__.py` — exports RESEARCHER_SYSTEM
  - `agents/prompts/researcher_prompt.py` — moved from agents/researcher_prompt.py

### Changed
- **agents/__init__.py**: Now re-exports RESEARCHER_SYSTEM from prompts/
- **supervisor/dag_validator.py**: Imports EXECUTION_ROLES and SYNTHESIS_ROLES from config/agents.py instead of hardcoding

### Changed
- **Memory access control revizuit complet:**
  - Memory agent (DAG) are acces **doar la Working (Redis)** — bridge de comunicare
  - DAG agents nu mai au acces direct la Episodic (ChromaDB) sau Long-term (Letta)
  - Dacă un DAG agent are nevoie de info din straturile profunde → face query către GOAT
  - GOAT filtrează și decide ce informații să returneze
  - **Zero halucinații** — memory agent nu primește date nefiltrate
- **Documentație actualizată:**
  - `readme.md` — arhitectură, access control, memory agent flow
  - `docs/architecture.md` — diagramă, flow, access levels
  - `memory/README.md` — three-tier memory, access control, data flow
  - `SESSION_NOTES.md` — notițe curente despre configurație
