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
from memory.config import L3_MIN_GUARANTEE_TOKENS, TEMPORAL_L3_GUARANTEE_TOKENS
from memory.context_assembler import format_facts

__all__ = ["MIND_TOOLS_HIDDEN", "MIND_GUIDANCE", "NO_GATE", "on", "hidden_tools", "assemble"]

MIND_TOOLS_HIDDEN = {"search_memory", "read_l1"}

# The tool gate's verdict when the mind is on: the gated tool is already hidden, nothing is decided per turn.
NO_GATE = {"hidden": False}

MIND_GUIDANCE = (
    "Blocul [În minte] de mai sus este memoria ta: ce îți amintești acum despre Laris și despre ce s-a întâmplat. "
    "Dacă ceva nu e acolo, spune simplu că nu-ți amintești; nu inventa. Nu căuta niciodată în memorie cu unelte, "
    "cu shell-ul sau prin fișiere. Tu decizi singur ce merită păstrat, fără să ceri confirmare: o regulă nouă, o "
    "preferință sau un fapt stabil despre Laris ori despre lucru îl salvezi cu promote_memory, iar un episod care "
    "merită amintit, cu store_memory. Când Laris corectează un fapt din Reguli sau Ce știu, actualizează-l cu "
    "promote_memory (aceeași cheie, valoare nouă) sau scoate-l cu forget_fact dacă nu mai e adevărat."
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

    The facts stay mandatory, as in today's prompt (R26): L2 is sized from the budget minus the tokens of every L1
    fact under the block's ``Reguli`` and ``Ce știu`` headers (an upper bound: knowledge facts show only when in
    mind), so the block gets that reserve plus the L3 guarantee, not the guarantee minus the facts. The turns the L2 block shows go to expose as ``exclude_ids`` (their l3_ids): never shown twice (R31).

    ``l3_results`` are retrieve()'s, before any gap filter (R15). Returns (blocks, l3_used = memories shown,
    ids shown, {id: text shown}, True). When expose gives no block (it failed), the blocks are today's: the real
    facts and the original ``l3_results`` go through ``assemble_context`` as with the mind off, and the last value
    is False, so the caller leaves out MIND_GUIDANCE (it would deny memories); the rest stays as with the mind on."""
    def ctx(results: list[dict], known: dict, size: int):
        return layers.assemble_context(chat_id, budget=size, l3_results=results, facts=known, messages=messages,
                                       identity_prompt=identity_prompt, temporal_center=temporal_center,
                                       provenance=provenance)
    # every fact, under both headers (rules always, knowledge when in mind): an upper bound of what the block shows
    known = estimate_tokens("Reguli:\n\n\nCe știu:\n" + format_facts(facts)) if facts else 0
    need = known + (TEMPORAL_L3_GUARANTEE_TOKENS if temporal_center is not None else L3_MIN_GUARANTEE_TOKENS)
    size = max(0, int(budget) - known)
    blocks, _ = await ctx([], {}, size)
    left = max(0, int(budget) - sum(estimate_tokens(b) for b in blocks))
    if left < need:  # L2's cap counts message text only, its timestamps go over it: size L2 once more, smaller
        blocks, _ = await ctx([], {}, max(0, size - (need - left)))
        left = max(0, int(budget) - sum(estimate_tokens(b) for b in blocks))
    blocks = list(blocks)
    l2 = next((b for b in blocks if b.startswith("[Conversation History]")), "")
    in_l2 = {str(m["l3_id"]) for m in messages or []
             if m.get("l3_id") and f"{m.get('role')}: {m.get('content')}" in l2}
    block, shown, texts = await mind_live.expose(llm, getattr(layers, "_episodic", None), chat_id, lane, message,
                                                 l3_results, facts, left, exclude_ids=in_l2)
    if not block:
        blocks, l3_used = await ctx(l3_results, facts, budget)
        return list(blocks), l3_used, [], {}, False
    return [*blocks, block], len(shown), list(shown), dict(texts), True
