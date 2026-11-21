"""orchestrator.mind_hook — the switch-dependent pieces of a turn when the working memory is on (spec §3, "Fără cârje").

With ``[mind] enabled`` (config/brain.toml) the prompt's memory is ONE ``[În minte]`` block built by
``brain.mind_live.expose``; the L1 facts and the L3 memories go there, not into the context blocks a second time.
GOAT gets no memory-search tools: a gap in what is in mind is a defect for us to fix, not one to work around.
Off (the default), nothing here changes the turn.
"""
from __future__ import annotations

from brain import brain as _brain
from brain import mind_live
from memory.budget import estimate_tokens

__all__ = ["MIND_TOOLS_HIDDEN", "MIND_GUIDANCE", "NO_GATE", "on", "hidden_tools", "assemble"]

MIND_TOOLS_HIDDEN = {"search_memory", "read_l1"}

# The tool gate's verdict when the mind is on: the gated tool is already hidden, nothing is decided per turn.
NO_GATE = {"hidden": False}

MIND_GUIDANCE = (
    "Blocul [În minte] de mai sus este memoria ta: ce îți amintești acum despre Laris și despre ce s-a întâmplat. "
    "Dacă ceva nu e acolo, spune simplu că nu-ți amintești; nu inventa. Nu căuta niciodată în memorie cu unelte, "
    "cu shell-ul sau prin fișiere. Folosește store_memory, promote_memory și forget_fact doar când Laris îți cere "
    "explicit („ține minte”, „uită”)."
)


def on() -> bool:
    """True when the working memory replaces the old memory path this turn."""
    return _brain.mind_on()


def hidden_tools() -> set[str]:
    """The memory-search tools GOAT no longer gets when the mind is on (neither offered nor callable); else none."""
    return set(MIND_TOOLS_HIDDEN) if on() else set()


async def assemble(llm, layers, chat_id: str, lane: str, message: str, *, budget: int, l3_results: list[dict],
                   facts: dict, messages: list[dict], identity_prompt: str, temporal_center, provenance: dict,
                   ) -> tuple[list[str], int, list[str], dict[str, str]]:
    """The context blocks without L1 facts and L3 results, then the ``[În minte]`` block in the budget left.

    ``l3_results`` are retrieve()'s, before any gap filter (R15). Returns (blocks, l3_used = memories shown,
    ids shown, {id: text shown}); a failed expose leaves the blocks as they are and shows nothing."""
    blocks, _ = await layers.assemble_context(
        chat_id, budget=budget, l3_results=[], facts={}, messages=messages,
        identity_prompt=identity_prompt, temporal_center=temporal_center, provenance=provenance,
    )
    blocks = list(blocks)
    left = max(0, int(budget) - sum(estimate_tokens(b) for b in blocks))
    block, shown, texts = await mind_live.expose(llm, getattr(layers, "_episodic", None), chat_id, lane, message,
                                                 l3_results, facts, left)
    if block:
        blocks.append(block)
    return blocks, len(shown), list(shown), dict(texts)
