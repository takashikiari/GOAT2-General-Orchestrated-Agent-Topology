import { describe, expect, it } from 'vitest'
import { FRAME_SAMPLES, TARGET_RATE, toPcm16 } from './pcmCapture'

describe('toPcm16', () => {
  it('converts float samples to 16-bit and clamps', () => {
    const out = toPcm16(new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]), TARGET_RATE)
    expect(Array.from(out)).toEqual([0, 16383, -16384, 32767, -32768, 32767, -32768])
  })

  it('resamples from a higher rate to 16 kHz (48 kHz gives a third of the samples)', () => {
    const input = new Float32Array(4800).fill(0.25)
    const out = toPcm16(input, 48000)
    expect(out.length).toBe(1600)
    expect(out[10]).toBe(Math.round(0.25 * 0x7fff - 0.5) || out[10]) // constant signal stays constant
    expect(new Set(out).size).toBe(1)
  })

  it('keeps a ramp monotonic when resampling', () => {
    const input = Float32Array.from({ length: 480 }, (_, i) => i / 480)
    const out = Array.from(toPcm16(input, 44100))
    expect(out.every((v, i) => i === 0 || v >= out[i - 1])).toBe(true)
  })

  it('a frame is 250 ms at 16 kHz', () => {
    expect(FRAME_SAMPLES / TARGET_RATE).toBeCloseTo(0.25)
  })
})
