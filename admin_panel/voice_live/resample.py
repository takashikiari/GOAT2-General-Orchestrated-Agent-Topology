"""admin_panel.voice_live.resample — 16-bit mono PCM from one sample rate to another, frame by frame.

The page sends the microphone at 16 kHz (what Gemini Live and Deepgram take); OpenAI Realtime accepts PCM only at
24 kHz. Linear interpolation is enough for speech going into a speech model, and the resampler keeps its position and
last sample between frames, so frame boundaries add no clicks and no drift.
"""
from __future__ import annotations

import array

__all__ = ["Resampler"]


class Resampler:
    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.step = in_rate / out_rate  # input samples per output sample
        self._pos = 0.0  # where the next output sample falls, counted from the previous frame's last sample
        self._last: int | None = None
        self._same = in_rate == out_rate

    def __call__(self, pcm: bytes) -> bytes:
        if self._same or not pcm:
            return pcm
        samples = array.array("h", pcm[: len(pcm) // 2 * 2])
        # the previous frame's last sample is index 0 here, so interpolation runs across the boundary
        src = ([self._last] if self._last is not None else []) + samples.tolist()
        out = array.array("h")
        pos = self._pos
        limit = len(src) - 1
        while pos < limit:
            left = int(pos)
            frac = pos - left
            out.append(int(round(src[left] * (1 - frac) + src[left + 1] * frac)))
            pos += self.step
        self._pos = pos - limit
        self._last = src[-1]
        return out.tobytes()
