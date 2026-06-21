"""
memory — three-tier conversation memory for GOAT 2.0.

Each tier is a subpackage with room to grow its own connection logic,
TTL handling, and serialisation without restructuring later:

    memory/working/   — current session (Redis, built now)
    memory/episodic/  — across sessions (future)
    memory/permanent/ — long-term knowledge (future)

Import the tier you need directly:
    from memory.working import WorkingMemory
"""
