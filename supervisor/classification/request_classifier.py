"""Lightweight request classifier for direct tool bypass.

Identifies queries that can be answered by a single tool call without
invoking the full DAG pipeline. Uses rule-based pattern matching only
— no LLM calls — for minimal latency.

DIRECT TOOL MAPPING:
====================
- memory_recent: queries about recent memory items, last stored facts
- memory_get: queries retrieving specific named facts from memory
- file_read: queries reading specific files by name/path

SAFETY CONSTRAINTS:
===================
- Conservative matching: only clear single-intent queries bypass DAG
- Ambiguous or multi-step requests always use full pipeline
- All patterns are case-insensitive and support Romanian/English
"""
from __future__ import annotations
import re
from typing import Literal

__all__ = ["DirectRequest", "classify_direct_request"]

DirectTool = Literal["memory_recent", "memory_get", "file_read"]


class DirectRequest:
    """Result of direct-request classification.

    Attributes:
        is_direct: True if query can be handled by single tool
        tool: Selected tool name (only if is_direct=True)
        extracted_param: Extracted parameter (file path, memory key, etc.)
        confidence: Match confidence score (0.0–1.0)
    """

    def __init__(
        self,
        is_direct: bool = False,
        tool: DirectTool | None = None,
        extracted_param: str | None = None,
        confidence: float = 0.0,
    ) -> None:
        self.is_direct = is_direct
        self.tool = tool
        self.extracted_param = extracted_param
        self.confidence = confidence

    def __bool__(self) -> bool:
        return self.is_direct


# ── Pattern definitions (case-insensitive) ──
# Note: Word boundaries (\b) may not work reliably with Romanian characters
# Using flexible patterns instead for broader coverage

_MEMORY_RECENT_PATTERNS = [
    r"(recent|last|latest)\s+(memory|items|facts|entries|stored)",
    r"(recent|last|latest)\s+(fact|facts)",
    r"(ce|what)\s+(am|do\s+i)\s+(în|in)\s+memorie",
    r"(ce\s+este|what\s+is)\s+(în|in)\s+(memorie|memory)",
    r"(memorie|memory)\s+(recentă|recent|ultimă|last)",
    r"(ultimele|last)\s+(intrări|entries|elemente|items)",
    r"(arată|show|afișează|display)\s+(memoria|memory)\s+(recentă|recent)",
    r"(verifică|verifica|check|afirmă)\s+(memoria|memory)\s+(recentă|recent|ultimă|last)",
    r"(show|afișează)\s+(me\s+)?(the\s+)?(recent|last|latest)\s+(stored|memory)",
    r"(ce\s+este|what\s+is)\s+(în|in)\s+(memorie|memory)\s+(recentă|recent)",
    r"(arată|show)\s+(ce|what)\s+(am|do\s+i)\s+(în|in)\s+(memorie|memory)",
    r"(ce\s+este|what\s+is)\s+(în|in)\s+(memorie|memory)\.",
    # ── Broader Romanian patterns (patch 72) ──
    r"(verifică|verifica|check)\s+(memoria|memory)",                       # "check memory"
    r"(arată|afișează|raportează)\s+(memoria|memory)",                     # "show/report memory"
    r"(raportează|report)\s+(memoria|memory)\s+(recentă|recent)",          # "report recent memory"
    r"(ai|aveți|am)\s+(în|in)\s+(memorie|memory)",                        # "you have in memory"
]

_MEMORY_GET_PATTERNS = [
    r"(get|retrieve|fetch|ia|extrage)\s+(fact|value|data|information)\s+(from\s+)?memory",
    r"(memory|memorie)\s+(get|ia|extrage|retrieve)\s+\w+",
    r"(what\s+is|care\s+este)\s+the\s+(fact|value)\s+(for\s+)?['\"]?\w+['\"]?",
]

_FILE_READ_PATTERNS = [
    r"(read|open|show|display|citește|afișează)\s+(file|fișier)\s+['\"]?[^\s'\"]+['\"]?",
    r"(file|fișier)\s+(read|citește|open|deschide)\s+['\"]?[^\s'\"]+['\"]?",
    r"['\"]?[a-zA-Z0-9_\-./]+\.(py|md|txt|json|yaml|yml|toml|cfg|ini|log|csv)['\"]?",
    r"(show|afișează|arătă)\s+content\s+(of|din)\s+['\"]?[^\s'\"]+['\"]?",
]

# Multi-step indicators that should NOT bypass DAG
_MULTI_STEP_INDICATORS = [
    r"\b(and|și)\b",
    r"\b(then|apoi|după)\b",
    r"\b(explain|explică)\s+(why|how|de ce|cum)\b",
    r"\b(analyze|analyse|analizează)\s+\w+",
    r"\b(compare|compară)\s+\w+",
    r"\b(difference|diferență)\s+between\b",
    r"\b(why|de ce)\s+(did|does|was|is|are)\b",
    r"\bhow\s+(to|do|can|could|should|would)\b",
    r"\b(suggest|recommend|propune|optimize|refactor)\s+\w+",
]


def _count_pattern_matches(text: str, patterns: list[str]) -> int:
    """Count how many patterns match the text."""
    count = 0
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            count += 1
    return count


def _extract_file_path(text: str) -> str | None:
    """Extract file path from query text."""
    # Match quoted paths
    quoted = re.search(r"['\"]([a-zA-Z0-9_\-./]+\.(py|md|txt|json|yaml|yml|toml|cfg|ini|log|csv))['\"]", text)
    if quoted:
        return quoted.group(1)
    # Match unquoted paths after file-related keywords
    path_match = re.search(r"(file|fișier|read|citește)\s+([a-zA-Z0-9_\-./]+\.(py|md|txt|json|yaml|yml|toml|cfg|ini|log|csv))", text, re.IGNORECASE)
    if path_match:
        return path_match.group(2)
    return None


def _has_multi_step_intent(text: str) -> bool:
    """Check if query contains multi-step or analytical intent."""
    return _count_pattern_matches(text, _MULTI_STEP_INDICATORS) > 0


def classify_direct_request(query: str) -> DirectRequest:
    """Classify whether a query can be handled by a single direct tool.

    Args:
        query: User's message text

    Returns:
        DirectRequest with is_direct=True if single-tool bypass is safe

    CLASSIFICATION LOGIC:
    =====================
    1. Reject immediately if multi-step indicators present
    2. Score each direct-tool pattern category
    3. Return highest-scoring category if confidence >= 0.5
    4. Default to is_direct=False (safe fallback)

    CONFIDENCE THRESHOLDS:
    ======================
    - memory_recent: >= 1 pattern match
    - memory_get: >= 1 pattern match + named key detected
    - file_read: >= 1 pattern match + valid file path extracted
    """
    text = query.strip().lower()

    # Safety: reject multi-step queries immediately
    if _has_multi_step_intent(text):
        return DirectRequest(is_direct=False)

    # Score each category
    recent_score = _count_pattern_matches(text, _MEMORY_RECENT_PATTERNS)
    get_score = _count_pattern_matches(text, _MEMORY_GET_PATTERNS)
    file_score = _count_pattern_matches(text, _FILE_READ_PATTERNS)

    # Determine best match
    best_score = max(recent_score, get_score, file_score)

    if best_score == 0:
        return DirectRequest(is_direct=False)

    # Memory recent: simple threshold
    if recent_score >= 1 and recent_score >= get_score and recent_score >= file_score:
        return DirectRequest(
            is_direct=True,
            tool="memory_recent",
            confidence=min(1.0, recent_score * 0.5),
        )

    # Memory get: requires named key indicator
    if get_score >= 1 and get_score >= file_score:
        # Check for quoted key name
        has_key = bool(re.search(r"['\"][^'\"]+['\"]", text))
        if has_key:
            return DirectRequest(
                is_direct=True,
                tool="memory_get",
                extracted_param=re.search(r"['\"]([^'\"]+)['\"]", text).group(1),
                confidence=min(1.0, get_score * 0.5),
            )

    # File read: requires valid file path
    if file_score >= 1:
        file_path = _extract_file_path(text)
        if file_path:
            return DirectRequest(
                is_direct=True,
                tool="file_read",
                extracted_param=file_path,
                confidence=min(1.0, file_score * 0.5),
            )

    return DirectRequest(is_direct=False)
