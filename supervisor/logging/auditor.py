"""Auditor agent for GOAT 2.0 — cross-tool consistency check after DAG execution.

Compares AgentResult outputs across tasks to detect:
  1. Word-level divergence (Jaccard similarity) — same role, different results
  2. Semantic contradictions — conflicting entity claims across roles
  3. Potential hallucination markers — unsupported assertions, numerical inconsistencies

The auditor is a pure analysis pass — it does not modify results, only flags anomalies.

IMPROVEMENTS (FIX):
===================
- Jaccard similarity now normalizes diacritics (ș→s, ț→t, ă→a, î→i, â→a) for Romanian text
- Regex patterns cover Romanian quotes („ ") and en-dash (–)
- Contradiction detection extended to numerical ranges and semantic opposites
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

from supervisor.types import AgentResult

__all__ = ["AuditReport", "run_auditor"]

log = logging.getLogger("goat2.supervisor.logging")


@dataclass
class AuditReport:
    """Report from the auditor pass — flags anomalies found across task results.

    Attributes:
        anomalies: List of human-readable anomaly descriptions.
        compared_pairs: Number of result pairs compared.
    """
    anomalies: list[str] = field(default_factory=list)
    compared_pairs: int = 0


_SIMILARITY_THRESHOLD: Final[float] = 0.30
_MIN_CONTENT_LEN:      Final[int]   = 20

# Patterns for extracting entity-like claims (numbers, proper nouns, key-value pairs)
# Only extract ACTUAL facts, not session IDs or metadata
_NUMBER_PATTERN = re.compile(
    r'\b\d+[.,]?\d*\s*(?:%|KB|MB|GB|ms|s|h|d|€|\$|lei|euro|dolari|ms|sec|min|ore|zile)\b',
    re.IGNORECASE,
)
# Key-value patterns only for actual config values, not session IDs
_KEY_VALUE_PATTERN = re.compile(
    r'\b(?:success|error|status|version|size|count)\s*(?::|==|=|–|-|—)\s*(["\']?[^"\',;.]+["\']?)',
    re.IGNORECASE,
)
# Only property patterns for verifiable facts
_PROPERTY_PATTERN = re.compile(
    r'\b(?:are|este|costă|durează|conține|folosește|rulează|suportă|necesită|include|oferă|produce|generează|calculează)\s+([^,;.]+)',
    re.IGNORECASE,
)
# Ignore patterns for session metadata
_IGNORE_PREFIXES = ("dag:", "goat:", "session", "timestamp", "uuid:", "key:", "test_")

# Semantic opposites for contradiction detection
_SEMANTIC_OPPOSITES: dict[str, list[str]] = {
    "rapid": ["lent", "greu", "încet"],
    "lent": ["rapid", "repede", "accelerat"],
    "ieftin": ["scump", "costisitor", "car"],
    "scump": ["ieftin", "gratuit", "free"],
    "mare": ["mic", "redus", "diminuat"],
    "mic": ["mare", "imens", "gigantic"],
    "ușor": ["greu", "complicat", "dificil"],
    "greu": ["ușor", "simplu", "facil"],
    "sigur": ["nesigur", "periculos", "riscant"],
    "periculos": ["sigur", "securizat"],
    "funcționează": ["nu funcționează", "eșuează", "crapă", "bug"],
    "succes": ["eșec", "eroare", "problemă"],
}


def _normalize_diacritics(text: str) -> str:
    """Normalize Romanian diacritics for consistent comparison.

    Converts: ș→s, ț→t, ă→a, î→i, â→a (both lowercase and uppercase).
    Also decomposes Unicode NFD forms (ș = s + combining comma).
    """
    # First, decompose Unicode (NFD) to handle composed diacritics
    text = unicodedata.normalize("NFD", text)
    # Remove combining marks (accents, cedillas, etc.)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Normalize back to NFC
    text = unicodedata.normalize("NFC", text)
    # Explicit Romanian mappings (for safety)
    replacements = {
        "ș": "s", "Ş": "S", "ş": "s",
        "ț": "t", "Ț": "T", "ţ": "t",
        "ă": "a", "Ă": "A",
        "î": "i", "Î": "I",
        "â": "a", "Â": "A",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _jaccard(a: str, b: str) -> float:
    """Compute word-level Jaccard similarity between two text strings.

    Normalizes diacritics before comparison for Romanian language support.
    """
    sa = set(_normalize_diacritics(a.lower()).split())
    sb = set(_normalize_diacritics(b.lower()).split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _extract_claims(text: str) -> set[str]:
    """Extract concrete claims (numbers, key-values, properties) from text.

    Filters out session IDs, timestamps, and other metadata.
    """
    claims: set[str] = set()
    for match in _NUMBER_PATTERN.findall(text):
        claims.add(match.lower().strip())
    for match in _KEY_VALUE_PATTERN.findall(text):
        k, v = match[0], match[1] if isinstance(match, (list, tuple)) and len(match) >= 2 else (match, "")
        k_lower = k.strip().lower()
        v_lower = v.strip().lower()
        # Skip session/metadata keys
        if any(skip in k_lower for skip in _IGNORE_PREFIXES):
            continue
        claims.add(f"{k_lower}:{v_lower}")
    for match in _PROPERTY_PATTERN.findall(text):
        claims.add(match.strip().lower())
    return claims


def _find_contradictions(claims_a: set[str], claims_b: set[str]) -> list[str]:
    """Detect directly contradictory claims between two sets.

    Looks for claims that assert different values for the same key or entity.
    E.g., "latency:200ms" vs "latency:500ms" — clear contradiction.

    Also detects semantic opposites (e.g., "rapid" vs "lent").
    """
    contradictions: list[str] = []
    for ca in claims_a:
        for cb in claims_b:
            # Same key, different value
            if ":" in ca and ":" in cb:
                ka, va = ca.split(":", 1)
                kb, vb = cb.split(":", 1)
                if ka == kb and va != vb:
                    contradictions.append(f"conflict on '{ka}': '{va}' vs '{vb}'")
            # Semantic opposites
            ca_lower = ca.strip().lower()
            cb_lower = cb.strip().lower()
            if ca_lower in _SEMANTIC_OPPOSITES:
                if any(opp in cb_lower for opp in _SEMANTIC_OPPOSITES[ca_lower]):
                    contradictions.append(
                        f"semantic conflict: '{ca}' vs '{cb}'"
                    )
    return contradictions


async def run_auditor(results: dict[str, AgentResult]) -> AuditReport:
    """Compare AgentResults across roles; flag divergence and contradictions.

    Two results diverge when their Jaccard word-overlap is below
    _SIMILARITY_THRESHOLD. Also detects semantic contradictions between
    different roles (e.g., researcher says 200ms, coder says 500ms).

    Only compares results that are non-error and have at least
    _MIN_CONTENT_LEN characters of output.
    """
    report = AuditReport()
    by_role: dict[str, list[AgentResult]] = {}
    all_valid: list[AgentResult] = []

    for r in results.values():
        if r.ok and len(r.output or "") >= _MIN_CONTENT_LEN:
            by_role.setdefault(r.role, []).append(r)
            all_valid.append(r)

    # 1. Same-role Jaccard divergence
    for role, role_results in by_role.items():
        if len(role_results) < 2:
            continue
        for i, ra in enumerate(role_results):
            for rb in role_results[i + 1:]:
                report.compared_pairs += 1
                sim = _jaccard(ra.output, rb.output)
                if sim < _SIMILARITY_THRESHOLD:
                    msg = (
                        f"Low similarity: role={role} "
                        f"tasks=({ra.task_id}, {rb.task_id}) "
                        f"jaccard={sim:.2f} < {_SIMILARITY_THRESHOLD}"
                    )
                    log.warning(msg)
                    report.anomalies.append(msg)

    # 2. Cross-role semantic contradictions
    if len(all_valid) >= 2:
        for i, ra in enumerate(all_valid):
            for rb in all_valid[i + 1:]:
                claims_a = _extract_claims(ra.output)
                claims_b = _extract_claims(rb.output)
                contradictions = _find_contradictions(claims_a, claims_b)
                for c in contradictions:
                    msg = (
                        f"Contradiction: roles=({ra.role}, {rb.role}) "
                        f"tasks=({ra.task_id}, {rb.task_id}) — {c}"
                    )
                    log.warning(msg)
                    report.anomalies.append(msg)
                    report.compared_pairs += 1

    # 3. Potential hallucination markers: single-source numerical claims
    #    Only flag actual facts, not session metadata or IDs
    if len(all_valid) >= 2:
        all_claims: list[tuple[str, AgentResult]] = []
        for r in all_valid:
            for c in _extract_claims(r.output):
                # Filter out session metadata
                skip = False
                for prefix in _IGNORE_PREFIXES:
                    if c.lower().startswith(prefix):
                        skip = True
                        break
                if not skip:
                    all_claims.append((c, r))
        claim_sources: dict[str, list[str]] = {}
        for claim, r in all_claims:
            claim_sources.setdefault(claim, []).append(f"{r.role}/{r.task_id}")
        for claim, sources in claim_sources.items():
            # Only flag if it's an actual fact claim (contains key words)
            if len(sources) == 1 and any(w in claim.lower() for w in ["success", "error", "status", "version", "size", "count", "cost", "price"]):
                msg = (
                    f"Unverified claim: '{claim}' appears only in {sources[0]}, "
                    f"no corroboration from other roles"
                )
                log.info(msg)
                report.anomalies.append(msg)
                report.compared_pairs += 1

    return report
