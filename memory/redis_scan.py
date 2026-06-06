from __future__ import annotations

from memory.types import AgentRole, MemoryKey


async def scan_keys(r: object, pattern: str, prefix: str) -> list[MemoryKey]:
    """SCAN all Redis keys matching pattern; strips prefix to recover the logical MemoryKey."""
    found: list[MemoryKey] = []
    cursor = 0
    while True:
        cursor, batch = await r.scan(cursor, match=pattern, count=100)  # type: ignore[union-attr]
        for full_key in batch:
            found.append(MemoryKey(full_key[len(prefix):]))
        if cursor == 0:
            break
    return found


async def scan_delete(r: object, pattern: str) -> int:
    """SCAN all Redis keys matching pattern and delete them in a single bulk DEL call."""
    all_keys: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await r.scan(cursor, match=pattern, count=100)  # type: ignore[union-attr]
        all_keys.extend(batch)
        if cursor == 0:
            break
    return await r.delete(*all_keys) if all_keys else 0  # type: ignore[union-attr]
