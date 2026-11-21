"""orchestrator.mind_hook — the switch-dependent pieces of a turn when the working memory is on (spec §3, "Fără cârje").

With ``[mind] enabled`` (config/brain.toml) the prompt's memory is ONE ``[În minte]`` block built by
``brain.mind_live.expose``; the L1 facts and the L3 memories go there, not into the context blocks a second time.
GOAT gets no memory-search tools: a gap in what is in mind is a defect for us to fix, not one to work around.
Off (the default), nothing here changes the turn. When ``expose`` fails (no block), ``assemble`` builds today's
context blocks (real facts, original L3 results) and says the block is missing (R21): only the memory context
returns; the memory-search tools stay hidden and their guidance stays out (no crutch: a failed expose is a defect).
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
                   ) -> tuple[list[str], int, list[str], dict[str, str], bool]:
    """The context blocks without L1 facts and L3 results, then the ``[În minte]`` block in the budget left.

    ``l3_results`` are retrieve()'s, before any gap filter (R15). Returns (blocks, l3_used = memories shown,
    ids shown, {id: text shown}, True). When expose gives no block (it failed), the blocks are today's: the real
    facts and the original ``l3_results`` go through ``assemble_context`` as with the mind off, and the last value
    is False, so the caller leaves out MIND_GUIDANCE (it would deny memories); the rest stays as with the mind on."""
    def ctx(results: list[dict], known: dict):
        return layers.assemble_context(chat_id, budget=budget, l3_results=results, facts=known, messages=messages,
                                       identity_prompt=identity_prompt, temporal_center=temporal_center,
                                       provenance=provenance)
    blocks, _ = await ctx([], {})
    blocks = list(blocks)
    left = max(0, int(budget) - sum(estimate_tokens(b) for b in blocks))
    block, shown, texts = await mind_live.expose(llm, getattr(layers, "_episodic", None), chat_id, lane, message,
                                                 l3_results, facts, left)
    if not block:
        blocks, l3_used = await ctx(l3_results, facts)
        return list(blocks), l3_used, [], {}, False
    return [*blocks, block], len(shown), list(shown), dict(texts), True
