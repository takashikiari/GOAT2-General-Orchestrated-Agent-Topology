import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FIRST_AUDIO_MS, createRemoteVoice, remoteVoiceAvailable } from './remoteVoice'

/** WebSocket stand-in: the test plays the server. */
class FakeSocket {
  static last: FakeSocket
  binaryType = ''
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close = vi.fn(() => {
    this.readyState = 3
  })
  constructor() {
    FakeSocket.last = this
  }
  send(data: string) {
    this.sent.push(data)
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
  json(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  pcm(samples: number[]) {
    const buf = new ArrayBuffer(samples.length * 2)
    const view = new DataView(buf)
    samples.forEach((s, i) => view.setInt16(i * 2, s, true))
    this.onmessage?.({ data: buf })
  }
}

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
  finish() {
    this.onended?.()
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
  close = vi.fn(async () => {
    this.state = 'closed'
  })
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

const tick = () => new Promise<void>((r) => setTimeout(r, 0))
const make = () => createRemoteVoice(() => new FakeSocket() as unknown as WebSocket)
const ctx = () => FakeContext.instances[0]
const socket = () => FakeSocket.last

beforeEach(() => {
  FakeContext.instances = []
  vi.stubGlobal('AudioContext', FakeContext)
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('remote voice (Deepgram, streamed through the panel)', () => {
  it('authenticates, sends the text, and plays the audio chunks one after the other without gaps', async () => {
    const voice = make()
    const speaking = voice.speak('Bună ziua.')
    socket().open()
    expect(JSON.parse(socket().sent[0])).toMatchObject({ type: 'auth' })
    expect(JSON.parse(socket().sent[1])).toEqual({ type: 'speak', text: 'Bună ziua.' })
    socket().json({ type: 'ready', sample_rate: 24000 })
    socket().pcm(new Array(2400).fill(16384)) // 0.1 s
    socket().pcm(new Array(4800).fill(-16384)) // 0.2 s
    const [a, b] = ctx().sources
    expect(a.startedAt).toBeCloseTo(1.12, 5) // a short lead over "now"
    expect(b.startedAt).toBeCloseTo(a.startedAt! + 0.1, 5) // right where the first ends
    expect(a.buffer!.data[0]).toBeCloseTo(0.5, 3)
    expect(b.buffer!.data[0]).toBeCloseTo(-0.5, 3)
    socket().json({ type: 'done' })
    a.finish()
    b.finish()
    expect(await speaking.done).toBe('ended')
    expect(socket().close).toHaveBeenCalled()
  })

  it('does not end while there is still sound to play after the server is done', async () => {
    const speaking = make().speak('x')
    socket().open()
    socket().pcm([1, 2, 3, 4])
    socket().json({ type: 'done' })
    let ended = false
    void speaking.done.then(() => { ended = true })
    await tick()
    expect(ended).toBe(false)
    ctx().sources[0].finish()
    await tick()
    expect(ended).toBe(true)
  })

  it('a frame that ends in the middle of a sample is completed by the next one', () => {
    make().speak('x')
    socket().open()
    const bytes = new Uint8Array([0x00, 0x40, 0x00]) // 0x4000 then half a sample
    socket().onmessage?.({ data: bytes.buffer })
    socket().onmessage?.({ data: new Uint8Array([0x40]).buffer }) // the other half: 0x4000 again
    const [a, b] = ctx().sources
    expect(a.buffer!.data.length).toBe(1)
    expect(b.buffer!.data.length).toBe(1)
    expect(b.buffer!.data[0]).toBeCloseTo(0.5, 3)
  })

  it('is "unavailable" when the server reports an error before any sound, so another voice can take over', async () => {
    const speaking = make().speak('x')
    socket().open()
    socket().json({ type: 'error', code: 'not_configured', message: 'no' })
    expect(await speaking.done).toBe('unavailable')
  })

  it('is "unavailable" when the connection fails or closes without sound', async () => {
    const failed = make().speak('x')
    socket().onerror?.()
    expect(await failed.done).toBe('unavailable')
    const closed = make().speak('y')
    socket().onclose?.()
    expect(await closed.done).toBe('unavailable')
  })

  it('is "unavailable" when the socket cannot even be created', async () => {
    const voice = createRemoteVoice(() => { throw new Error('blocked') })
    expect(await voice.speak('x').done).toBe('unavailable')
  })

  it('gives up when no sound arrives in time', async () => {
    vi.useFakeTimers()
    const speaking = make().speak('x')
    socket().open()
    await vi.advanceTimersByTimeAsync(FIRST_AUDIO_MS + 10)
    expect(await speaking.done).toBe('unavailable')
    expect(socket().close).toHaveBeenCalled()
  })

  it('a connection lost after some sound just lets what is already scheduled play, and ends (no second voice)', async () => {
    const speaking = make().speak('x')
    socket().open()
    socket().pcm([1, 2, 3, 4])
    socket().onclose?.()
    ctx().sources[0].finish()
    expect(await speaking.done).toBe('ended')
  })

  it('cancel stops the sound, tells the server and closes, ending quietly', async () => {
    const speaking = make().speak('x')
    socket().open()
    socket().pcm([1, 2, 3, 4])
    speaking.cancel()
    expect(ctx().sources[0].stopped).toBe(true)
    expect(socket().sent.map((m) => JSON.parse(m).type)).toContain('cancel')
    expect(socket().close).toHaveBeenCalled()
    expect(await speaking.done).toBe('ended')
  })

  it('cancel before any sound is a cancel, not a failure', async () => {
    const speaking = make().speak('x')
    speaking.cancel()
    expect(await speaking.done).toBe('ended')
  })

  it('priming creates the audio context and resumes it (inside the tap that starts the conversation)', () => {
    const voice = make()
    voice.prime()
    expect(ctx().resume).toHaveBeenCalled()
    voice.speak('x')
    expect(FakeContext.instances.length).toBe(1) // the same context is used for the replies
  })

  it('priming never throws, and speaking without audio support is "unavailable"', async () => {
    vi.stubGlobal('AudioContext', class { constructor() { throw new Error('too many contexts') } })
    const voice = make()
    expect(() => voice.prime()).not.toThrow()
    expect(await voice.speak('x').done).toBe('unavailable')
  })

  it('close releases the audio context, and a later use makes a new one', () => {
    const voice = make()
    voice.prime()
    voice.close()
    expect(ctx().close).toHaveBeenCalled()
    voice.prime()
    expect(FakeContext.instances.length).toBe(2)
  })

  it('is available where there are WebSockets and Web Audio, including the old webkit name', () => {
    expect(remoteVoiceAvailable()).toBe(true)
    vi.stubGlobal('AudioContext', undefined)
    ;(window as unknown as { webkitAudioContext: unknown }).webkitAudioContext = FakeContext
    expect(remoteVoiceAvailable()).toBe(true)
    delete (window as unknown as { webkitAudioContext?: unknown }).webkitAudioContext
    expect(remoteVoiceAvailable()).toBe(false)
  })
})
