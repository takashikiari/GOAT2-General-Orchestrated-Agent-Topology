# GOAT Changelog

## [Unreleased]

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
