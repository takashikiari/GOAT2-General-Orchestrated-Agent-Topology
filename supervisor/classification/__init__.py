"""Intent classification for GOAT 2.0 — depth routing and language detection.

Exports:
    - IntentDepth: Enum for conversational/analytical/complex routing
    - classify_intent: LLM-driven intent depth classification
    - DirectRequest, DirectTool: Lightweight direct-request classification
    - classify_direct_request: Rule-based single-tool bypass detection
    - detect_language: LLM-driven language detection
"""
from supervisor.classification.classifier import IntentDepth, classify_intent
from supervisor.classification.request_classifier import DirectRequest, DirectTool, classify_direct_request
from supervisor.classification.lang_detect import detect_language

__all__ = [
    "IntentDepth",
    "classify_intent",
    "DirectRequest",
    "DirectTool",
    "classify_direct_request",
    "detect_language",
]