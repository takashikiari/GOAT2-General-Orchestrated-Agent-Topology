import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TAIL_MS, createPcmPlayer, pcmPlayerAvailable } from './pcmPlayer'

class FakeSource {
  buffer: { duration: number; data: Float32Array } | null = null
  startedAt: number | null = null
  stopped = false
  onended: (() => void) | null = null
  connect = vi.fn()
  start(when: number) {
    this.startedAt = when
  }
  stop() {
    this.stopped = true
  }
}

class FakeContext {
  static instances: FakeContext[] = []
  currentTime = 1
  state = 'suspended'
  destination = {}
  sources: FakeSource[] = []
  resume = vi.fn(async () => {
    this.state = 'running'
  })
  close = vi.fn(async () => {})
  constructor() {
    FakeContext.instances.push(this)
  }
  createBuffer(_channels: number, length: number, rate: number) {
    const data = new Float32Array(length)
    return { duration: length / rate, data, copyToChannel: (src: Float32Array) => data.set(src) }
  }
  createBufferSource() {
    const s = new FakeSource()
    this.sources.push(s)
    return s
  }
}

const pcm = (samples: number[]) => {
  const buf = new ArrayBuffer(samples.length * 2)
  samples.forEach((s, i) => new DataView(buf).setInt16(i * 2, s, true))
  return buf
}
const ctx = () => FakeContext.instances[0]

beforeEach(() => {
  FakeContext.instances = []
  vi.useFakeTimers()
  vi.stubGlobal('AudioContext', FakeContext)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('pcm player', () => {
  it('plays chunks back to back, and reports when sound starts and (after a short tail) when it stopped', () => {
    const playing = vi.fn()
    const player = createPcmPlayer(playing)
    player.setRate(24000)
    player.push(pcm(new Array(2400).fill(16384))) // 0.1 s
    player.push(pcm(new Array(4800).fill(-16384))) // 0.2 s
    const [a, b] = ctx().sources
    expect(playing).toHaveBeenCalledTimes(1)
    expect(playing).toHaveBeenCalledWith(true)
    expect(a.startedAt).toBeCloseTo(1.1, 5) // a short lead over "now"
    expect(b.startedAt).toBeCloseTo(a.startedAt! + 0.1, 5) // exactly where the first ends
    expect(a.buffer!.data[0]).toBeCloseTo(0.5, 3)
    expect(b.buffer!.data[0]).toBeCloseTo(-0.5, 3)
    a.onended?.()
    b.onended?.()
    expect(playing).toHaveBeenCalledTimes(1) // not yet: a next chunk may still follow
    vi.advanceTimersByTime(TAIL_MS + 1)
    expect(playing).toHaveBeenLastCalledWith(false)
  })

  it('a chunk that arrives during the tail keeps it playing', () => {
    const playing = vi.fn()
    const player = createPcmPlayer(playing)
    player.push(pcm([1, 2, 3, 4]))
    ctx().sources[0].onended?.()
    vi.advanceTimersByTime(TAIL_MS - 50)
    player.push(pcm([5, 6]))
    vi.advanceTimersByTime(TAIL_MS)
    expect(playing).toHaveBeenCalledTimes(1) // still the first "true": the second chunk is not over
  })

  it('completes a sample that was split between two frames', () => {
    const player = createPcmPlayer(() => {})
    player.push(new Uint8Array([0x00, 0x40, 0x00]).buffer) // 0x4000 and half a sample
    player.push(new Uint8Array([0x40]).buffer)
    const [a, b] = ctx().sources
    expect(a.buffer!.data.length).toBe(1)
    expect(b.buffer!.data[0]).toBeCloseTo(0.5, 3)
  })

  it('clear stops everything at once and says it stopped', () => {
    const playing = vi.fn()
    const player = createPcmPlayer(playing)
    player.push(pcm([1, 2, 3, 4]))
    player.push(pcm([1, 2]))
    player.clear()
    expect(ctx().sources.every((s) => s.stopped)).toBe(true)
    expect(playing).toHaveBeenLastCalledWith(false)
    player.push(pcm([1, 2]))
    expect(ctx().sources[2].startedAt).toBeCloseTo(1.1, 5) // the queue starts again from "now"
  })

  it('priming creates and resumes the context, never throws, and close releases it', () => {
    const player = createPcmPlayer(() => {})
    player.prime()
    expect(ctx().resume).toHaveBeenCalled()
    player.close()
    expect(ctx().close).toHaveBeenCalled()
    vi.stubGlobal('AudioContext', class { constructor() { throw new Error('too many contexts') } })
    expect(() => createPcmPlayer(() => {}).prime()).not.toThrow()
  })

  it('is available where there is Web Audio, including the old webkit name', () => {
    expect(pcmPlayerAvailable()).toBe(true)
    vi.stubGlobal('AudioContext', undefined)
    ;(window as unknown as { webkitAudioContext: unknown }).webkitAudioContext = FakeContext
    expect(pcmPlayerAvailable()).toBe(true)
    delete (window as unknown as { webkitAudioContext?: unknown }).webkitAudioContext
    expect(pcmPlayerAvailable()).toBe(false)
  })
})
