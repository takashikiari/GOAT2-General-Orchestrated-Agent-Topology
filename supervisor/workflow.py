"""WorkflowGraph — executes AgentTask DAG in topological waves via Kahn's algorithm.

Tasks in the same wave run concurrently, bounded by a shared semaphore.
Populates AgentResult with source provenance tracking and tool parameter validation.

GOAT supervisor manages memory read/write directly