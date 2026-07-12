"""tests.test_observability_collector — tokens_injected must count guidance
text and tool schemas, not just the three assemble_blocks blocks.

Regression test for the observability gap found in the 2026-07-12 pipeline
audit: tokens_injected was computed purely from set_context_from_blocks
(identity/history/related blocks), but the orchestrator appends
_SEARCH_MEMORY_GUIDANCE/etc. text AFTER that measurement and separately
attaches serialized tool schemas to the actual API call — neither was ever
counted, so tokens_injected understated real prompt size (2.1x-3.1x on
single-call turns, 5x-10x on tool-round turns, per real-log measurement).
"""
from __future__ import annotations

from memory.observability_collector import ObservationCollector


_BLOCKS = [
    "[Identity]\nYou are GOAT.",
    "[Conversation History]\nuser: hi\nassistant: hello",
]


def _collector_with_blocks() -> ObservationCollector:
    c = ObservationCollector("chat1", "hi")
    c.set_context_from_blocks(_BLOCKS, results_found=0, results_used=0)
    return c


def test_tokens_injected_unchanged_when_no_guidance_or_tools():
    c = _collector_with_blocks()
    baseline = c.obs.tokens_injected
    c.add_prompt_extras("", [])
    assert c.obs.tokens_injected == baseline
    assert c.obs.tokens_guidance == 0
    assert c.obs.tokens_tools == 0


def test_tokens_injected_grows_with_guidance_text():
    c = _collector_with_blocks()
    baseline = c.obs.tokens_injected
    guidance = "search_memory is a last resort — only call it when there is NO episodic memory context."
    c.add_prompt_extras(guidance, [])
    assert c.obs.tokens_guidance > 0
    assert c.obs.tokens_injected == baseline + c.obs.tokens_guidance
    assert c.obs.budget_used == c.obs.tokens_injected


def test_tokens_injected_grows_with_tool_schemas():
    c = _collector_with_blocks()
    baseline = c.obs.tokens_injected
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "Search episodic memory for relevant past context.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "store_memory",
                "description": "Persist an important fact for future sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        },
    ]
    c.add_prompt_extras("", tool_schemas)
    assert c.obs.tokens_tools > 0
    assert c.obs.tokens_injected == baseline + c.obs.tokens_tools


def test_tool_round_turn_tracks_meaningfully_more_tokens_than_bare_blocks():
    """Same context blocks; the tool-configured turn's tokens_injected must
    exceed the guidance-and-tool-free turn's — the metric must actually be
    sensitive to what changed, not a constant pass-through of block tokens.
    """
    bare = _collector_with_blocks()
    bare.add_prompt_extras("", [])

    loaded = _collector_with_blocks()
    guidance = (
        "search_memory is a last resort — only call it when there is NO episodic "
        "memory context at all in this prompt."
        "\n\nYou can store important information for future sessions using the "
        "store_memory tool."
    )
    tool_schemas = [
        {"type": "function", "function": {
            "name": "search_memory", "description": "Search episodic memory.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }},
        {"type": "function", "function": {
            "name": "store_memory", "description": "Persist a fact.",
            "parameters": {"type": "object", "properties": {"content": {"type": "string"}}},
        }},
    ]
    loaded.add_prompt_extras(guidance, tool_schemas)

    assert loaded.obs.tokens_injected > bare.obs.tokens_injected
