"""orchestrator.tool_loop_config — agentic tool-calling loop config. Reads config/tools.toml ([tool_loop] section).

Moved out of memory/config.py + memory/config_extra.py on 2026-08-14: this
governs the orchestrator's tool-calling loop (round count, output-size caps,
evidence-preview formatting), not a memory-tier setting — it belongs beside
[shell]/[web]/etc. in tools.toml, not in memory.toml.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tools.toml"

_DEFAULTS: dict = {
    "tool_loop": {
        "max_iterations": 6,
        "max_output_chars": 60000,
        "result_short_threshold": 400,
        "result_head_chars": 200,
        "result_tail_chars": 150,
        "args_preview_chars": 200,
    }
}


def _load() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return _DEFAULTS


_cfg = _load().get("tool_loop", _DEFAULTS["tool_loop"])
_tl = _DEFAULTS["tool_loop"]

# Two independent hard backstops on the tool-calling loop (round COUNT and
# cumulative output SIZE) — see orchestrator.orchestrator._run_tool_round.
# Never a grounding decider: below both caps the model is called WITH tools
# so it can chain freely; at either cap tools are withheld and synthesis is
# forced. max_output_chars exists because a model issuing several large
# read_file/shell_run calls can blow the resent conversation past the
# model's context window well before max_iterations is reached (2026-07-09
# incident: 6 iterations reached 2.08M tokens in one turn and the API call
# was rejected outright).
AGENTIC_MAX_ITERATIONS: Final[int] = int(_cfg.get("max_iterations", _tl["max_iterations"]))
TOOL_ROUND_MAX_OUTPUT_CHARS: Final[int] = int(_cfg.get("max_output_chars", _tl["max_output_chars"]))

# Per-tool-call evidence preview formatting (orchestrator._compact_tool_summary).
# Results shorter than result_short_threshold chars are stored verbatim; longer
# ones are head/tail-truncated (head_tail-classified tools) or head-only.
TOOL_RESULT_SHORT_THRESHOLD: Final[int] = int(_cfg.get("result_short_threshold", _tl["result_short_threshold"]))
TOOL_RESULT_HEAD_CHARS: Final[int] = int(_cfg.get("result_head_chars", _tl["result_head_chars"]))
TOOL_RESULT_TAIL_CHARS: Final[int] = int(_cfg.get("result_tail_chars", _tl["result_tail_chars"]))
# Max chars of a tool call's JSON arguments shown in the evidence preview.
TOOL_ARGS_PREVIEW_CHARS: Final[int] = int(_cfg.get("args_preview_chars", _tl["args_preview_chars"]))

if AGENTIC_MAX_ITERATIONS <= 0:
    raise ValueError(f"[tool_loop] max_iterations ({AGENTIC_MAX_ITERATIONS}) must be > 0.")
if TOOL_ROUND_MAX_OUTPUT_CHARS <= 0:
    raise ValueError(f"[tool_loop] max_output_chars ({TOOL_ROUND_MAX_OUTPUT_CHARS}) must be > 0.")

__all__ = [
    "AGENTIC_MAX_ITERATIONS",
    "TOOL_ROUND_MAX_OUTPUT_CHARS",
    "TOOL_RESULT_SHORT_THRESHOLD",
    "TOOL_RESULT_HEAD_CHARS",
    "TOOL_RESULT_TAIL_CHARS",
    "TOOL_ARGS_PREVIEW_CHARS",
]
