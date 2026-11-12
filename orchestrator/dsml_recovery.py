"""orchestrator.dsml_recovery — what GOAT says when a tool round ends and the model still only "calls tools".

At the round cap (iterations or output size) deepseek-v4-flash sometimes answers with yet another DSML tool call; once
that markup is stripped nothing is left. That used to be replaced by a fixed English sentence ("I gathered some
information but couldn't put together a clean answer yet"), and everything the round found was lost (2026-11-12).
Now: one more call with no tools offered and an explicit "answer now from what you have" instruction; only if that is
still empty, a fallback in GOAT's language that says which checks ran, so "continuă" can pick up from there.
"""
from __future__ import annotations

from collections import Counter

from orchestrator.dsml import DSML_BLOCK, strip_dsml
from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["FINAL_ANSWER_INSTRUCTION", "fallback_text", "answer_without_tools"]

FINAL_ANSWER_INSTRUCTION = (
    "Nu mai poți apela nicio unealtă în tura asta. Răspunde ACUM, în limba conversației, doar din rezultatele de mai "
    "sus: spune ce ai aflat, ce n-ai apucat să verifici și ce ai verifica mai departe. Fără markup de unelte."
)


def fallback_text(calls: list[tuple[str, str, str]]) -> str:
    """The last resort, in Romanian (GOAT's language with Laris), naming the checks that ran."""
    counts = Counter(name for name, _, _ in calls)
    done = ", ".join(f"{name} ×{n}" if n > 1 else name for name, n in counts.most_common())
    ran = f"Am rulat {sum(counts.values())} verificări ({done}), " if counts else "Am început verificarea, "
    return ran + "dar n-am reușit să închei un răspuns în tura asta. Scrie „continuă” și reiau de unde am rămas."


async def answer_without_tools(llm, model: str, messages: list[dict], calls: list[tuple[str, str, str]],
                               chat_id: str, temperature: float, max_tokens: int) -> str:
    """One more completion with no tools and the final-answer instruction; the fallback text if it is still empty."""
    try:
        r = await llm.chat.completions.create(
            model=model, messages=list(messages) + [{"role": "user", "content": FINAL_ANSWER_INSTRUCTION}],
            temperature=temperature, max_tokens=max_tokens,
        )
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - the fallback below still answers
        log.warning("final answer without tools failed chat=%s: %s", chat_id, exc)
        text = ""
    if text and DSML_BLOCK.search(text):
        text = strip_dsml(text, chat_id)
    if text:
        log.info("final answer without tools recovered the reply chat=%s chars=%d", chat_id, len(text))
        return text
    log.warning("final answer without tools still empty chat=%s; fallback text", chat_id)
    return fallback_text(calls)
