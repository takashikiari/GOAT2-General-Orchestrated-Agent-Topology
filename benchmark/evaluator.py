"""benchmark.evaluator — score GOAT responses against expected answers.

Pure-Python evaluators with no ``orchestrator`` or ``memory`` imports.

* ``exact_match`` — word-boundary phrase presence (strict-ish, case-insensitive).
* ``fuzzy_match`` — paraphrase-tolerant match: normalizes ordinals (``14th``→
  ``14``), time formats (``9:00 AM`` / ``9 am`` → ``9am``) and punctuation, then
  checks the normalized expected phrase appears as a whole token in the
  normalized response. This is the primary recall grader — it tolerates the LLM
  rephrasing stored facts without the false positives of naive substring match.
* ``contains`` — every keyword present (multi-keyword OR-cases).
* ``semantic_similarity`` — Jaccard word-overlap in ``[0, 1]`` (offline).
* ``fact_in_results`` — grounding check: the expected fact is retrievable from
  L3 (appeared in retrieved episodic content), independent of the response.
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

# Normalization rules for ``fuzzy_match`` / ``fact_in_results``.
_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b")                  # 14th -> 14
_TIME_FULL = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)", re.I)  # 9:00 am -> 9am
_TIME_HOUR = re.compile(r"(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)", re.I)          # 9 am -> 9am
_PUNCT = re.compile(r"[^\w\s]")                                  # punctuation -> space


def _norm_ampm(token: str) -> str:
    """Canonicalize an am/pm marker: ``a.m.``/``AM`` → ``am``."""
    return token.lower().replace(".", "").replace(" ", "")


def _normalize(text: str) -> str:
    """Lowercase, strip ordinal suffixes, canonicalize times, drop punctuation.

    Examples: ``"9:00 AM"``→``"9am"``, ``"9 am"``→``"9am"``, ``"14th"``→``"14"``,
    ``"The National"``→``"the national"``. Whitespace is collapsed.
    """
    s = (text or "").lower()
    s = _ORDINAL.sub(r"\1", s)
    s = _TIME_FULL.sub(lambda m: m.group(1) + _norm_ampm(m.group(3)), s)
    s = _TIME_HOUR.sub(lambda m: m.group(1) + _norm_ampm(m.group(2)), s)
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split())


def _phrase_in(needle: str, haystack: str) -> bool:
    """Word-boundary search of normalized ``needle`` in normalized ``haystack``."""
    if not needle:
        return False
    return bool(re.compile(rf"\b{re.escape(needle)}\b").search(haystack))


class Evaluator:
    """Evaluate GOAT responses against expected answers."""

    @staticmethod
    def exact_match(response: str, expected: str) -> bool:
        """Case-insensitive word-boundary phrase presence (no normalization).

        True when the exact ``expected`` phrase appears in ``response`` as a
        whole-word match (``\\b…\\b``), case-insensitive. "Exact match" is read
        as "the exact expected answer matches/appears" — so it works for
        conversational responses (sentences, not bare tokens), multi-word
        phrases ("blue marlin"), and tokens that must not substring-match
        ("42" does not match "142"). Returns ``False`` when ``expected`` is
        empty. For paraphrase-tolerant grading (time formats, ordinals) see
        ``fuzzy_match``.
        """
        if not expected:
            return False
        pat = re.compile(rf"\b{re.escape(expected.lower())}\b")
        return bool(pat.search((response or "").lower()))

    @staticmethod
    def fuzzy_match(response: str, expected: str) -> bool:
        """Paraphrase-tolerant match — normalized expected phrase in response.

        Both sides are normalized (``_normalize``: ordinals, time formats,
        punctuation) then checked with a word boundary so ``"42"`` does not
        match ``"142"``. Tolerates the LLM rephrasing a stored fact: expected
        ``"9am"`` matches response ``"9:00 AM"``; expected ``"14th"`` matches
        ``"the 14th"``. Returns ``False`` when ``expected`` is empty.
        """
        if not expected:
            return False
        needle = _normalize(expected)
        if not needle:
            return False
        return _phrase_in(needle, _normalize(response))

    @staticmethod
    def contains(response: str, keywords: list[str]) -> bool:
        """True when every keyword appears in the response (case-insensitive)."""
        text = (response or "").lower()
        return all(kw.lower() in text for kw in (keywords or []))

    @staticmethod
    def fact_in_results(results: list[dict], expected: str) -> bool:
        """Grounding check: the expected fact is present in retrieved L3 content.

        ``results`` is a list of ``{"content", "metadata"}`` dicts from
        ``memory_layers`` search. Returns ``True`` when the normalized expected
        phrase appears (word-boundary) in any result's ``content`` — i.e. the
        memory system *could* have grounded the answer, independent of whether
        the model actually used it. Used to flag ungrounded-correct answers
        (correct response, but the fact was not retrievable → likely guessed).
        """
        if not expected or not results:
            return False
        needle = _normalize(expected)
        if not needle:
            return False
        return any(_phrase_in(needle, _normalize(r.get("content", ""))) for r in results)

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

    @staticmethod
    async def groundedness_judge(
        response: str, retrieved_context: str, llm_client: "AsyncOpenAI | None" = None,
        tool_evidence: str = "",
    ) -> dict:
        """Judge whether ``response`` is grounded in ``retrieved_context`` OR
        ``tool_evidence`` (spec §4.6).

        Returns ``{"grounded": bool | None, "hallucinated_claims": list[str],
        "answered_without_evidence": bool}``. ``grounded`` is ``None`` when no
        judge could run (no ``llm_client``, or the call/parse failed) — unknown,
        not false (spec §6: judge failures degrade rather than raising).
        Independent of ``expected_fact`` correctness: a response can be
        lexically correct yet contain an unsupported extra claim, or vice versa.

        ``tool_evidence`` (the turn's ``[Tool calls]`` summary, e.g. from
        ``Orchestrator.run``'s ``on_tool_summary`` callback) is a second, equally
        valid grounding source. Real-data manual review (2026-07-09): of 20
        judge-flagged "hallucinations" from a run where GOAT had read_file/
        shell_run/get_recent_logs available, ~60% were verified byte-for-byte
        accurate against the real log/source — the judge only ever saw memory
        context, never tool output, so a correct tool-sourced claim was
        indistinguishable from an invented one. Without this parameter,
        enabling tools makes the benchmark's hallucination rate look WORSE
        even when real hallucination drops.
        """
        if llm_client is None:
            return {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}
        from config import settings
        from utils.llm_utils import extract_json
        system = (
            "You are a strict fact-checking grader. Compare the RESPONSE against "
            "the RETRIEVED_CONTEXT (the assistant's memory) and the TOOL_EVIDENCE "
            "(results of tools like read_file/shell_run/get_recent_logs the "
            "assistant called this turn) — BOTH are valid grounding sources, not "
            "just RETRIEVED_CONTEXT. Reply with ONLY a JSON object: "
            '{"grounded": true/false, "hallucinated_claims": ["..."], '
            '"answered_without_evidence": true/false}. hallucinated_claims lists '
            "any specific claims in RESPONSE not supported by RETRIEVED_CONTEXT or "
            "TOOL_EVIDENCE. "
            "Do NOT list a self-referential statement about the assistant's own "
            "uncertainty or lack of memory (e.g. \"I don't remember that\", "
            "\"I don't have this information\") as a hallucinated claim — those "
            "are honest non-answers, not fabricated facts. "
            "answered_without_evidence is true when RESPONSE answers confidently "
            "despite RETRIEVED_CONTEXT and TOOL_EVIDENCE both being empty or "
            "irrelevant."
        )
        user = (
            f"RETRIEVED_CONTEXT:\n{retrieved_context or '(empty)'}\n\n"
            f"TOOL_EVIDENCE:\n{tool_evidence or '(none)'}\n\n"
            f"RESPONSE:\n{response}"
        )
        try:
            r = await llm_client.chat.completions.create(
                model=settings.MODEL_NAME,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.0, max_tokens=1024,
            )
            parsed = extract_json(r.choices[0].message.content or "")
            raw_claims = parsed.get("hallucinated_claims") or []
            hallucinated_claims = raw_claims if isinstance(raw_claims, list) else [str(raw_claims)]
            return {
                "grounded": bool(parsed.get("grounded", False)),
                "hallucinated_claims": hallucinated_claims,
                "answered_without_evidence": bool(parsed.get("answered_without_evidence", False)),
            }
        except Exception as exc:  # noqa: BLE001 — judge failure must not crash a run
            log.warning("groundedness_judge failed: %s", exc)
            return {"grounded": None, "hallucinated_claims": [], "answered_without_evidence": False}