"""admin_panel.voice_live.provider — what every speech-to-speech provider adapter looks like.

The events mirror what Gemini Live really sends (``serverContent`` audio parts, input/output transcription,
``interrupted``, ``turnComplete``, ``toolCall``, ``goAway``, ``sessionResumptionUpdate``, ``usageMetadata``), so another
provider fits by translating its messages into these, and nothing above the adapter knows which one is talking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, Union

__all__ = [
    "Audio", "UserText", "AssistantText", "ToolCall", "ToolCancel", "Interrupted", "TurnComplete", "GoAway",
    "Resumption", "Usage", "ProviderError", "Event", "HistoryItem", "LiveConfig", "Provider",
]


@dataclass(frozen=True)
class Audio:
    pcm: bytes  # 16-bit little-endian mono at ``LiveConfig.out_rate``


@dataclass(frozen=True)
class UserText:
    """What the provider heard the person say (it can lag behind a tool call). ``final`` closes a segment."""
    text: str
    final: bool = False


@dataclass(frozen=True)
class AssistantText:
    """What the model says, as text (for the chat bubble)."""
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCancel:
    """The provider withdrew these calls (the person talked over the model while they ran)."""
    ids: tuple[str, ...]


@dataclass(frozen=True)
class Interrupted:
    """The model's current response was cut off: everything queued for playback is stale."""


@dataclass(frozen=True)
class TurnComplete:
    """The model finished speaking its response."""


@dataclass(frozen=True)
class GoAway:
    """The connection will be closed by the provider soon."""
    seconds: float = 0.0


@dataclass(frozen=True)
class Resumption:
    """A handle that lets a closed session continue where it stopped."""
    handle: str


@dataclass(frozen=True)
class Usage:
    total_tokens: int = 0


@dataclass(frozen=True)
class ProviderError:
    code: str
    message: str


Event = Union[Audio, UserText, AssistantText, ToolCall, ToolCancel, Interrupted, TurnComplete, GoAway, Resumption, Usage, ProviderError]


@dataclass(frozen=True)
class HistoryItem:
    role: str  # "user" or "model"
    text: str


@dataclass(frozen=True)
class LiveConfig:
    system_prompt: str
    tools: tuple[dict, ...]  # neutral declarations: {"name", "description", "parameters"}
    voice: str | None = None
    in_rate: int = 16000
    out_rate: int = 24000


class Provider(Protocol):
    """One live conversation with a speech-to-speech model."""

    async def connect(self, config: LiveConfig, history: list[HistoryItem]) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def send_tool_result(self, call_id: str, name: str, result: str, scheduling: str) -> None:
        """``scheduling``: ``WHEN_IDLE`` (say it when the model is free) or ``SILENT`` (know it, do not say it now)."""

    async def interrupt(self) -> None: ...

    def events(self) -> AsyncIterator[Event]: ...

    async def close(self) -> None: ...
