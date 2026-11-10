import { describe, expect, it, vi } from 'vitest'
import { FRAME_SAMPLES, MAX_GAIN, MIN_GAIN, TARGET_RATE, buildInputChain, clampGain, toPcm16 } from './pcmCapture'

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

/** An AudioContext stand-in that records the nodes it makes and how they are wired. */
function fakeContext() {
  const made: { kind: string; node: Record<string, unknown> }[] = []
  const param = (value = 0) => ({ value })
  const node = (kind: string, extra: Record<string, unknown>) => {
    const n = { connect: vi.fn((to: unknown) => to), disconnect: vi.fn(), ...extra }
    made.push({ kind, node: n })
    return n
  }
  const ctx = {
    createGain: () => node('gain', { gain: param(1) }),
    createDynamicsCompressor: () =>
      node('limiter', { threshold: param(), knee: param(), ratio: param(), attack: param(), release: param() }),
  }
  return { ctx: ctx as unknown as AudioContext, made }
}

describe('the microphone gain', () => {
  it('at 1 the microphone goes straight to the capture, untouched (the Deepgram path)', () => {
    const { ctx, made } = fakeContext()
    const source = { connect: vi.fn() } as unknown as AudioNode
    const chain = buildInputChain(ctx, source, 1)
    expect(chain.output).toBe(source)
    expect(made).toEqual([])
  })

  it('above 1 it goes source → gain → limiter, the limiter catching peaks before they clip', () => {
    const { ctx, made } = fakeContext()
    const source = { connect: vi.fn() } as unknown as AudioNode
    const chain = buildInputChain(ctx, source, 1.8)
    const [gain, limiter] = made
    expect(made.map((m) => m.kind)).toEqual(['gain', 'limiter'])
    expect((gain.node.gain as { value: number }).value).toBe(1.8)
    expect(source.connect).toHaveBeenCalledWith(gain.node)
    expect(gain.node.connect).toHaveBeenCalledWith(limiter.node)
    expect(chain.output).toBe(limiter.node)
    const v = (k: string) => (limiter.node[k] as { value: number }).value
    expect(v('threshold')).toBeLessThan(0) // dBFS below full scale
    expect(v('ratio')).toBeGreaterThanOrEqual(12) // a limiter, not a gentle compressor
    expect(v('knee')).toBe(0)
    chain.setGain(2.2) // the slider (or a later setting) changes it live
    expect((gain.node.gain as { value: number }).value).toBe(2.2)
  })

  it('is kept within a sane range', () => {
    expect([clampGain(0.2), clampGain(1.8), clampGain(9), clampGain(Number.NaN)]).toEqual([MIN_GAIN, 1.8, MAX_GAIN, 1])
    expect(MIN_GAIN).toBe(1)
    expect(MAX_GAIN).toBeGreaterThanOrEqual(2)
  })
})
