"""admin_panel.voice_live.transcript — what Laris said in a live conversation, so none of it is lost (Laris, 2026-11-10).

The voice model only hands GOAT its own wording of a request (``ask_goat``), and small talk it answers by itself never
reaches GOAT at all. So the session keeps every final transcript of Laris's words:

- each one is written to the log (``voice_live: Laris said (transcript): …``), to compare what was said with what
  reached GOAT's memory;
- when the model calls ``ask_goat``, the words heard since the previous request go with it, verbatim, so they take
  the normal road of a chat message (L2, then L3/Chroma, and the Telegram copy); if that request does not reach GOAT
  (busy, refused) its words are put back and go with the next one;
- what is left when the conversation ends (small talk) goes to GOAT as ONE chat turn, so it reaches memory the same
  way without a GOAT turn per sentence.

The text stays within what the chat accepts (``ChatService`` refuses longer messages); the newest words are kept.
"""
from __future__ import annotations

from utils.logging.setup import get_logger

log = get_logger(__name__)

__all__ = ["Heard"]


class Heard:
    NOTE_HEAD = "(Din conversația vocală Live, de reținut.)"

    def __init__(self, max_chars: int = 6000) -> None:
        self._pending: list[str] = []
        self._max = max_chars

    def add(self, text: str) -> None:
        """A final transcript of what Laris said."""
        text = (text or "").strip()
        if text:
            self._pending.append(text)
            log.info("voice_live: Laris said (transcript): %s", text)

    def take(self) -> list[str]:
        """The words heard since they were last taken (for the request being sent now)."""
        words, self._pending = self._pending, []
        return words

    def restore(self, words: list[str]) -> None:
        """The request did not reach GOAT (busy, refused): its words wait for the next one, or for the end note."""
        self._pending = list(words) + self._pending

    def with_words(self, request: str, words: list[str]) -> str:
        """``request`` with Laris's own words, verbatim."""
        quoted = self._quote(words)
        return f"{request}\n\n(Laris a spus, transcris: {quoted})" if quoted else request

    def end_note(self) -> str | None:
        """The words that never went to GOAT, as one chat turn for the end of the conversation (None: there are none)."""
        quoted = self._quote(self.take())
        return f"{self.NOTE_HEAD} Laris a spus, transcris: {quoted}" if quoted else None

    def _quote(self, words: list[str]) -> str:
        quoted = [f"«{text}»" for text in words]
        kept: list[str] = []
        size = 0
        for q in reversed(quoted):  # the newest words matter most
            if kept and size + len(q) + 1 > self._max:
                break
            kept.append(q if kept else q[-self._max:])
            size += len(q) + 1
        return " ".join(reversed(kept))
