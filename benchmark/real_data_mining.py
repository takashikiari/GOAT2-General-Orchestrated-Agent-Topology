"""benchmark.real_data_mining — LLM-generated recall ground truth from real snapshot content (spec §4.2).

One registry.llm_client call per candidate asks for a natural follow-up recall
question and the short exact fact an answer must contain. Results cache to
disk (benchmark/data/real_recall_cases.json by convention, gitignored) so
mining runs once, not on every benchmark invocation.
"""
from __future__ import annotations

import json
from pathlib import Path

from benchmark.mining_candidates import select_candidates
from utils.llm_utils import extract_json
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["generate_case", "mine_cases", "load_or_mine"]

_SYSTEM_PROMPT = (
    "You are generating a benchmark case from a real stored memory. Given the "
    "CONTENT below, produce a natural follow-up question a user might ask "
    "later to recall this information, and the short exact fact string an "
    "answer must contain to be correct. Reply with ONLY a JSON object: "
    '{"query": "...", "expected_fact": "..."}.'
)


async def generate_case(entry: dict, llm_client) -> dict | None:
    """One LLM call generating (query, expected_fact) for a mined candidate.

    Returns ``None`` (logged) on any call/parse/empty-field failure — the
    caller skips this candidate rather than aborting the batch (spec §6).
    """
    from config import settings
    content = entry.get("content", "")
    try:
        r = await llm_client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTENT:\n{content}"},
            ],
            temperature=0.4, max_tokens=200,
        )
        parsed = extract_json(r.choices[0].message.content or "")
        query = str(parsed["query"]).strip()
        expected_fact = str(parsed["expected_fact"]).strip()
        if not query or not expected_fact:
            raise ValueError("empty query or expected_fact")
    except Exception as exc:  # noqa: BLE001 — one bad candidate must not abort mining
        log.warning("generate_case failed id=%s: %s", entry.get("id"), exc)
        return None
    metadata = entry.get("metadata") or {}
    return {
        "id": entry["id"],
        "message_id": metadata.get("message_id") or entry["id"],
        "chat_id_source": metadata.get("chat_id", ""),
        "lead_in_turns": [content],
        "query": query,
        "expected_fact": expected_fact,
    }


async def mine_cases(entries: list[dict], llm_client) -> list[dict]:
    """Select candidates from ``entries`` and generate a case for each that succeeds."""
    cases = []
    for entry in select_candidates(entries):
        case = await generate_case(entry, llm_client)
        if case is not None:
            cases.append(case)
    return cases


async def load_or_mine(
    entries: list[dict], llm_client, cache_path: Path, force: bool = False,
) -> list[dict]:
    """Return cached mined cases from ``cache_path``, mining fresh only when needed."""
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text())
    cases = await mine_cases(entries, llm_client)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cases, indent=2))
    return cases
