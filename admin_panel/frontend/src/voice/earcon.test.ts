import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EARCON_EVERY_MS, createEarcon } from './earcon'

function fakeContext() {
  const tones: number[] = []
  const param = () => ({ value: 0, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() })
  const ctx = {
    state: 'running',
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
    createGain: () => ({ gain: param(), connect: vi.fn() }),
    createOscillator: () => ({ type: '', frequency: { value: 0 }, connect: vi.fn(), start: () => tones.push(1), stop: vi.fn() }),
  }
  return { ctx: ctx as unknown as AudioContext, tones }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('earcon', () => {
  it('repeats a short tone while working and stops at once', () => {
    const { ctx, tones } = fakeContext()
    const e = createEarcon(() => ctx)
    e.start()
    expect(tones).toHaveLength(1) // the first tone right away
    vi.advanceTimersByTime(EARCON_EVERY_MS * 3)
    expect(tones).toHaveLength(4)
    e.stop()
    vi.advanceTimersByTime(EARCON_EVERY_MS * 3)
    expect(tones).toHaveLength(4)
    expect(e.playing).toBe(false)
  })

  it('never throws where the page has no sound', () => {
    const e = createEarcon(() => null)
    expect(() => { e.prime(); e.start(); e.stop(); e.close() }).not.toThrow()
  })
})
