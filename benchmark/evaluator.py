"""benchmark.evaluator — score GOAT responses against expected answers.

Pure-Python evaluators with no ``orchestrator`` or ``memory`` imports.

* ``exact_match`` / ``contains`` — deterministic lexical checks.
* ``semantic_similarity`` — Jaccard word-overlap score in ``[0, 1]``. Lexical
  (no embedding dependency) so it works offline and is deterministic; a vector
  variant can be plugged in by callers that have an embedding client.
* ``llm_judge`` — async; takes the LLM client as a parameter so the evaluator
  stays decoupled from the registry, and falls back to a lexical check when no
  client is supplied.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from utils.logging.setup import get_logger

if TYPE_CHECKING:
    from openai import AsyncOpenAI

log = get_logger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
# Yes/no is parsed from the judge's first non-empty line, lowercased.
_YES = re.compile(r"\byes\b")
_NO = re.compile(r"\bno\b")


class Evaluator:
    """Evaluate GOAT responses against expected answers."""

    @staticmethod
    def exact_match(response: str, expected: str) -> bool:
        """Case-insensitive word-boundary phrase presence.

        True when the exact ``expected`` phrase appears in ``response`` as a
        whole-word match (``\\b…\\b``), case-insensitive. "Exact match" is read
        as "the exact expected answer matches/appears" — so it works for
        conversational responses (sentences, not bare tokens), multi-word
        phrases ("blue marlin"), and tokens that must not substring-match
        ("42" does not match "142"). Returns ``False`` when ``expected`` is
        empty.
        """
        if not expected:
            return False
        pat = re.compile(rf"\b{re.escape(expected.lower())}\b")
        return bool(pat.search((response or "").lower()))

    @staticmethod
    def contains(response: str, keywords: list[str]) -> bool:
        """True when every keyword appears in the response (case-insensitive)."""
        text = (response or "").lower()
        return all(kw.lower() in text for kw in (keywords or []))

    @staticmethod
    def semantic_similarity(response: str, expected: str) -> float:
        """Jaccard word-overlap similarity in ``[0, 1]`` (lexical, offline).

        Both strings are lowercased and tokenised into alphanumeric words; the
        score is ``|intersection| / |union|``. Two empty strings score 1.0; one
        empty string scores 0.0.
        """
        a = set(_WORD.findall((response or "").lower()))
        b = set(_WORD.findall((expected or "").lower()))
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    async def llm_judge(
        response: str, expected: str, query: str,
        llm_client: "AsyncOpenAI | None" = None,
    ) -> dict:
        """Use an LLM to judge whether ``response`` answers ``query`` per ``expected``.

        Returns ``{"correct": bool, "reason": str}``. When ``llm_client`` is
        ``None`` (or ``expected`` is empty), falls back to a lexical check so
        the evaluator works without a live LLM.
        """
        if llm_client is None or not (expected or "").strip():
            ok = Evaluator.contains(response, [expected]) if expected else False
            return {"correct": ok, "reason": "lexical fallback (no llm_client or empty expected)"}
        from config import settings  # lazy — avoids import-time config read
        system = (
            "You are a strict grader. Decide whether the RESPONSE correctly "
            "answers the QUESTION using the EXPECTED answer as ground truth. "
            "Reply with exactly one line: 'yes' or 'no', then a short reason."
        )
        user = (
            f"QUESTION: {query}\nEXPECTED: {expected}\n"
            f"RESPONSE: {response}\n\nIs the response correct? yes/no:"
        )
        try:
            r = await llm_client.chat.completions.create(
                model=settings.MODEL_NAME,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.0, max_tokens=64,
            )
            text = (r.choices[0].message.content or "").strip().lower()
            first = next((ln for ln in text.splitlines() if ln.strip()), "")
            correct = bool(_YES.search(first)) and not _NO.search(first.split(":")[0])
            return {"correct": correct, "reason": text or "empty"}
        except Exception as exc:  # noqa: BLE001 — judge failure must not crash a run
            log.warning("llm_judge failed: %s", exc)
            ok = Evaluator.contains(response, [expected])
            return {"correct": ok, "reason": f"error: {exc}"}