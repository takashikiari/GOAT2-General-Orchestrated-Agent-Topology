from __future__ import annotations

import logging
from typing import Final

from memory.episodic.chroma_helpers import _str_to_tags
from memory.episodic.chroma_types import ChromaGetResult, ChromaQueryResult, ChromaStoredMetadata
from memory.shared.types import AgentRole, EntryId, IsoTimestamp, MemoryEntry, MemoryEntryMetadata

log = logging.getLogger("goat2.memory.chroma")

_SOURCE: Final[str] = "chroma"

_BLANK_META = ChromaStoredMetadata(
    agent_role="", key="", created_at="", created_at_ts=0, tags=""
)


def _row_to_entry(
    doc_id: EntryId, document: str,
    metadata: ChromaStoredMetadata, agent_role: AgentRole,
    *, score: float | None = None,
) -> MemoryEntry:
    """Convert one Chroma row into a MemoryEntry; score is cosine similarity [0,1] when provided."""
    tags_list = _str_to_tags(metadata.get("tags") or "")
    meta = MemoryEntryMetadata(
        tags=tags_list,
        created_at_ts=float(metadata.get("created_at_ts") or 0),
    )
    if score is not None:
        meta["score"] = round(score, 4)
    return MemoryEntry(
        id=doc_id, agent_role=agent_role,
        key=metadata.get("key") or "",  # type: ignore[arg-type]
        content=document, metadata=meta,
        created_at=IsoTimestamp(metadata.get("created_at") or ""),
        source=_SOURCE,
    )


def _parse_get_result(
    result: ChromaGetResult, agent_role: AgentRole,
) -> list[MemoryEntry]:
    # Pure — PyO3 candidate once ChromaDB returns typed structs
    """Map a Collection.get() result (parallel flat lists) to a list of MemoryEntry."""
    ids   = result.get("ids")       or []
    docs  = result.get("documents") or []
    metas = result.get("metadatas") or []
    return [
        _row_to_entry(EntryId(doc_id), doc or "", meta or _BLANK_META, agent_role)
        for doc_id, doc, meta in zip(ids, docs, metas)
    ]


def _parse_query_result(
    result: ChromaQueryResult, agent_role: AgentRole,
) -> list[MemoryEntry]:
    """Map a Collection.query() result to MemoryEntry list; sets metadata['score'] from distances."""
    ids_outer   = result.get("ids")       or [[]]
    docs_outer  = result.get("documents") or [[]]
    metas_outer = result.get("metadatas") or [[]]
    dists_outer = result.get("distances") or [[]]
    ids   = ids_outer[0]   if ids_outer   else []
    docs  = docs_outer[0]  if docs_outer  else []
    metas = metas_outer[0] if metas_outer else []
    dists = dists_outer[0] if dists_outer else []
    out = []
    for i, (doc_id, doc, meta) in enumerate(zip(ids, docs, metas)):
        score = max(0.0, 1.0 - float(dists[i])) if i < len(dists) else None
        out.append(_row_to_entry(EntryId(doc_id), doc or "", meta or _BLANK_META, agent_role, score=score))
    return out
