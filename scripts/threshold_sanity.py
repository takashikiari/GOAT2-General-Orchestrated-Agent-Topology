"""scripts.threshold_sanity — embed query pairs, print cosines vs the activation thresholds.

The activation turn-state is decided by cosine(query_emb, thread_centroid) crossing
``ACTIVATION_DRIFT_WARM`` / ``ACTIVATION_DRIFT_COLD``. Before trusting those
thresholds in production we must see where real query pairs actually land in the
bundled ONNX MiniLM vector space: do same-thread paraphrases clear 0.92? do
different-target queries fall below 0.70? is the middle band populated?

This is the gate the plan calls out: if same-thread paraphrases collide with
different-target queries at the chosen threshold, the warm/cold split is wrong
and we tune before going live. No corpus needed — only the embedding function.
"""
from __future__ import annotations

import asyncio

from memory.activation import cosine
from memory.config import (
    ACTIVATION_DRIFT_COLD,
    ACTIVATION_DRIFT_WARM,
    ACTIVATION_ENRICHING_SIM,
)
from memory.episodic import EpisodicMemory

# (label, query_a, query_b, expected_band)
#   same   → should be WARM  (cosine >= drift_warm)
#   drift  → middle band     (drift_cold <= cosine < drift_warm)
#   cold   → consensus shift (cosine < drift_cold AND lexical disjoint)
PAIRS = [
    ("same: identical rephrasing",      "what is my wifi password?",        "what is my wifi password?",            "same"),
    ("same: paraphrase",               "what is my wifi password?",        "what's the wifi password?",             "same"),
    ("same: follow-up",                 "what is my wifi password?",        "do you remember my wifi password?",    "same"),
    ("same: richer",                    "what is my wifi password?",        "can you tell me my wifi password again please", "same"),
    ("drift: related topic",            "what is my wifi password?",        "i cannot connect to the wifi network",  "drift"),
    ("cold: different intent",          "what is my wifi password?",        "how do i fix my home network",          "cold"),
    ("drift: same meta-topic (passwords)", "what is my wifi password?",      "what is my email password?",            "drift"),
    ("cold: different target, disjoint",      "what is my wifi password?", "what's my sister's name?",              "cold"),
    ("cold: fully disjoint",            "what is my sister's name?",       "what's the weather in tokyo today",    "cold"),
    ("drift: same entity, different facet", "what is my sister's name?",   "what is my sister's job?",              "drift"),
    ("drift: sister sub-topic",         "what is my sister's name?",        "tell me about my sister's family",      "drift"),
    ("same: identical",                 "remind me what time the meeting is", "remind me what time the meeting is", "same"),
    ("drift: meeting follow-up",        "remind me what time the meeting is", "is the meeting in the morning or afternoon", "drift"),
    ("cold: meeting vs lunch",          "remind me what time the meeting is", "what time is lunch scheduled",        "cold"),
]


def band(cos: float) -> str:
    if cos >= ACTIVATION_DRIFT_WARM:
        return "WARM"
    if cos >= ACTIVATION_DRIFT_COLD:
        return "DRIFT"
    return "COLD"


def _norm(expected: str) -> str:
    # "same" (same thread) means WARM; map the human label to the band name.
    return {"same": "warm", "drift": "drift", "cold": "cold"}[expected]


async def main() -> None:
    print(f"thresholds: warm>={ACTIVATION_DRIFT_WARM}  cold<{ACTIVATION_DRIFT_COLD}  "
          f"enriching>={ACTIVATION_ENRICHING_SIM}")
    print(f"{'pair':<52} {'cos':>6}  {'band':<5}  expected")
    print("-" * 86)
    epi = EpisodicMemory()
    # Warm the collection once before timing-sensitive measurement: the very
    # first embed hits a one-time ChromaDB tenant-init that can return None
    # (degrade-to-cold path), which would spuriously fail the first pair.
    for _ in range(3):
        if await epi.embed_query("warmup") is not None:
            break
    ok = True
    for label, a, b, expected in PAIRS:
        ea, eb = await asyncio.gather(epi.embed_query(a), epi.embed_query(b))
        if ea is None or eb is None:
            print(f"{label:<52} {'FAIL':>6}  embedding returned None")
            ok = False
            continue
        cos = cosine(ea, eb)
        got = band(cos)
        want = _norm(expected)
        mark = "OK " if got.lower() == want else "!! "
        print(f"{mark}{label:<49} {cos:6.3f}  {got:<5}  {expected}")
        if got.lower() != want:
            ok = False
    print("-" * 86)
    print("ALL PAIRS MATCH EXPECTED BAND" if ok else
          "MISMATCH — thresholds need tuning before production")


if __name__ == "__main__":
    asyncio.run(main())