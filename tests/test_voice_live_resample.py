"""tests.test_voice_live_resample — 16 kHz microphone frames to 24 kHz, without clicks or drift at frame boundaries."""
from __future__ import annotations

import array

from admin_panel.voice_live.resample import Resampler


def _pcm(values) -> bytes:
    return array.array("h", values).tobytes()


def _samples(pcm: bytes) -> list[int]:
    return array.array("h", pcm).tolist()


def test_a_frame_comes_out_one_and_a_half_times_as_long_at_the_same_level():
    out = _samples(Resampler(16000, 24000)(_pcm([500] * 1600)))
    assert abs(len(out) - 2400) <= 1 and set(out) == {500}  # the last one waits for the next frame


def test_many_frames_add_up_without_drift_and_a_ramp_stays_a_ramp_across_the_boundaries():
    r = Resampler(16000, 24000)
    ramp = list(range(0, 3200 * 4, 4))  # one long ramp, sent in 10 frames of 320 samples
    out: list[int] = []
    for i in range(0, len(ramp), 320):
        out += _samples(r(_pcm(ramp[i:i + 320])))
    assert abs(len(out) - len(ramp) * 1.5) <= 1  # no samples lost or invented between frames
    steps = {b - a for a, b in zip(out, out[1:])}
    assert steps <= {2, 3}  # 4 per input sample = about 2.67 per output sample: no jumps at the boundaries


def test_the_same_rate_and_empty_or_odd_frames_pass_through_safely():
    same = Resampler(24000, 24000)
    assert same(_pcm([1, 2, 3])) == _pcm([1, 2, 3])
    r = Resampler(16000, 24000)
    assert r(b"") == b""
    assert len(r(_pcm([7, 7]) + b"\x01")) % 2 == 0  # a stray byte is ignored, never half a sample
