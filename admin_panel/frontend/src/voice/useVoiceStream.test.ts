import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeMedia } from '../test/fakeMedia'

const cap = vi.hoisted(() => ({
  captures: [] as { onFrame: (b: ArrayBuffer) => void; paused: boolean; stopped: boolean }[],
  fail: false,
}))
vi.mock('./pcmCapture', () => ({
  startPcmCapture: vi.fn(async (_stream: unknown, onFrame: (b: ArrayBuffer) => void) => {
    if (cap.fail) throw Object.assign(new Error('worklet'), { name: 'NotSupportedError' })
    const c = { onFrame, paused: false, stopped: false }
    cap.captures.push(c)
    return { setPaused: (p: boolean) => { c.paused = p }, stop: () => { c.stopped = true } }
  }),
}))
vi.mock('../telegram/initData', () => ({ getInitData: () => 'signed-init-data' }))

import { END_OF_TURN_MS, useVoiceStream } from './useVoiceStream'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  readyState = 0
  binaryType = ''
  sent: (string | ArrayBuffer)[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => unknown) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) { FakeWebSocket.instances.push(this) }
  send(d: string | ArrayBuffer) { this.sent.push(d) }
  close() { this.readyState = 3; this.onclose?.() }
  open() { this.readyState = 1; this.onopen?.() }
  async push(obj: unknown) { await this.onmessage?.({ data: JSON.stringify(obj) }) }
}
const sock = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
const texts = (s: FakeWebSocket) => s.sent.filter((m): m is string => typeof m === 'string').map((m) => JSON.parse(m))

async function started(handlers = { onUtterance: vi.fn(), onActivity: vi.fn() }) {
  const hook = renderHook(() => useVoiceStream(handlers))
  await act(async () => { await hook.result.current.start() })
  await act(async () => { sock().open() })
  await act(async () => { await sock().push({ type: 'ready' }) })
  return { ...hook, handlers }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  cap.captures = []
  cap.fail = false
  vi.stubGlobal('WebSocket', FakeWebSocket)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useVoiceStream', () => {
  it('asks for the microphone, opens the socket on this page, authenticates, then listens on "ready"', async () => {
    const media = installFakeMedia()
    const { result } = renderHook(() => useVoiceStream({ onUtterance: vi.fn() }))
    await act(async () => { await result.current.start() })
    expect(media.getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({ echoCancellation: true, channelCount: 1 }) })
    expect(result.current.state).toBe('connecting')
    expect(sock().url).toMatch(/^ws:\/\/.*\/api\/voice\/stream$/)
    expect(sock().binaryType).toBe('arraybuffer')
    await act(async () => { sock().open() })
    expect(texts(sock())).toEqual([{ type: 'auth', initData: 'signed-init-data' }])
    expect(cap.captures).toHaveLength(0) // no audio before the server is ready
    await act(async () => { await sock().push({ type: 'ready' }) })
    expect(result.current.state).toBe('listening')
    expect(cap.captures).toHaveLength(1)
  })

  it('sends the audio frames; pausing releases the microphone and resuming opens it again', async () => {
    const media = installFakeMedia()
    const { result } = await started()
    const frame = new Int16Array(4000).buffer
    const binary = () => sock().sent.filter((m) => typeof m !== 'string')
    act(() => cap.captures[0].onFrame(frame))
    expect(binary()).toHaveLength(1)
    act(() => result.current.setPaused(true))
    // GOAT thinks and speaks with the microphone closed: an open one keeps the phone in its quiet call audio mode
    expect(cap.captures[0].stopped).toBe(true)
    expect(media.track.stop).toHaveBeenCalled()
    act(() => cap.captures[0].onFrame(frame))
    expect(binary()).toHaveLength(1)
    expect(sock().readyState).toBe(1) // the conversation goes on: the socket stays open
    await act(async () => { result.current.setPaused(false) })
    expect(media.getUserMedia).toHaveBeenCalledTimes(2)
    expect(cap.captures).toHaveLength(2)
    act(() => cap.captures[1].onFrame(frame))
    expect(binary()).toHaveLength(2)
    expect(result.current.state).toBe('listening')
  })

  it('a pause while the microphone is reopening keeps it closed', async () => {
    installFakeMedia()
    const { result } = await started()
    act(() => result.current.setPaused(true))
    await act(async () => {
      result.current.setPaused(false)
      result.current.setPaused(true)
    })
    expect(cap.captures.slice(1).every((c) => c.stopped)).toBe(true)
  })

  it('shows what is being said and hands it over only after END_OF_TURN_MS of silence, not at a short pause', async () => {
    installFakeMedia()
    const { result, handlers } = await started()
    vi.useFakeTimers()
    await act(async () => { await sock().push({ type: 'transcript', text: 'salut ce', final: false, speech_final: false }) })
    expect(result.current.interim).toBe('salut ce')
    await act(async () => { await sock().push({ type: 'transcript', text: 'salut, ce faci?', final: true, speech_final: true }) })
    expect(handlers.onUtterance).not.toHaveBeenCalled() // Deepgram's 400 ms pause is not the end of the turn
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS - 1) })
    expect(handlers.onUtterance).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(1) })
    expect(handlers.onUtterance).toHaveBeenCalledWith('salut, ce faci?')
    expect(result.current.interim).toBe('')
    expect(handlers.onActivity).toHaveBeenCalled()
  })

  it('a pause to think, then more words: still one message', async () => {
    installFakeMedia()
    const { handlers } = await started()
    vi.useFakeTimers()
    await act(async () => { await sock().push({ type: 'transcript', text: 'verifică logurile', final: true, speech_final: true }) })
    await act(async () => { await sock().push({ type: 'utterance_end' }) })
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS - 500) })
    await act(async () => { await sock().push({ type: 'speech_started' }) })
    await act(async () => { await sock().push({ type: 'transcript', text: 'din ultima', final: false }) })
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS - 500) })
    await act(async () => { await sock().push({ type: 'transcript', text: 'din ultima oră.', final: true, speech_final: true }) })
    expect(handlers.onUtterance).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS) })
    expect(handlers.onUtterance).toHaveBeenCalledTimes(1)
    expect(handlers.onUtterance).toHaveBeenCalledWith('verifică logurile din ultima oră.')
  })

  it('a sound with no words after it does not hold the message back for ever', async () => {
    installFakeMedia()
    const { handlers } = await started()
    vi.useFakeTimers()
    await act(async () => { await sock().push({ type: 'transcript', text: 'gata', final: true, speech_final: true }) })
    await act(async () => { await sock().push({ type: 'speech_started' }) }) // a cough
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS) })
    expect(handlers.onUtterance).toHaveBeenCalledWith('gata')
  })

  it('hears nothing while paused (GOAT thinking or speaking) and drops what was half heard', async () => {
    installFakeMedia()
    const { result, handlers } = await started()
    await act(async () => { await sock().push({ type: 'transcript', text: 'jumătate', final: true, speech_final: false }) })
    act(() => result.current.setPaused(true))
    expect(result.current.interim).toBe('')
    vi.useFakeTimers()
    await act(async () => { await sock().push({ type: 'transcript', text: 'ecou', final: true, speech_final: true }) })
    await act(async () => { await sock().push({ type: 'utterance_end' }) })
    act(() => { vi.advanceTimersByTime(END_OF_TURN_MS * 2) })
    expect(handlers.onUtterance).not.toHaveBeenCalled()
  })

  it('says why the server stopped it, in plain words', async () => {
    installFakeMedia()
    const { result } = await started()
    await act(async () => { await sock().push({ type: 'error', code: 'not_configured', message: 'x' }) })
    expect(result.current.error).toMatch(/not set up on the server/)
    expect(result.current.state).toBe('idle')
    expect(cap.captures[0].stopped).toBe(true)
  })

  it('maps the other server errors and falls back to the server message', async () => {
    installFakeMedia()
    for (const [code, expected] of [['unauthorized', /Session expired/], ['upstream_auth', /rejected/], ['upstream', /not reachable/], ['time_limit', /30 minute/], ['weird', /custom text/]] as const) {
      FakeWebSocket.instances = []
      const { result, unmount } = await started()
      await act(async () => { await sock().push({ type: 'error', code, message: 'custom text' }) })
      expect(result.current.error).toMatch(expected)
      unmount()
    }
  })

  it('reports a refused microphone and never opens a socket', async () => {
    installFakeMedia({ getUserMedia: async () => { throw Object.assign(new Error('x'), { name: 'NotAllowedError' }) } })
    const { result } = renderHook(() => useVoiceStream({ onUtterance: vi.fn() }))
    await act(async () => { await result.current.start() })
    expect(result.current.error).toMatch(/permission denied/i)
    expect(result.current.state).toBe('idle')
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('treats an unexpected close as a failure, and a failed audio setup too', async () => {
    installFakeMedia()
    const first = await started()
    act(() => sock().close())
    expect(first.result.current.error).toMatch(/connection closed/)
    first.unmount()
    cap.fail = true
    installFakeMedia()
    const second = renderHook(() => useVoiceStream({ onUtterance: vi.fn() }))
    await act(async () => { await second.result.current.start() })
    await act(async () => { sock().open() })
    await act(async () => { await sock().push({ type: 'ready' }) })
    expect(second.result.current.state).toBe('idle')
    expect(second.result.current.error).toBeTruthy()
  })

  it('stop ends the stream cleanly: tells the server, closes the socket, releases the microphone and the capture', async () => {
    const media = installFakeMedia()
    const { result } = await started()
    act(() => result.current.stop())
    const sentTexts = texts(sock())
    expect(sentTexts[sentTexts.length - 1]).toEqual({ type: 'close' })
    expect(sock().readyState).toBe(3)
    expect(media.track.stop).toHaveBeenCalled()
    expect(cap.captures[0].stopped).toBe(true)
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull() // a deliberate stop is not an error
  })

  it('does not start without microphone access or WebSocket support', async () => {
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
    const { result } = renderHook(() => useVoiceStream({ onUtterance: vi.fn() }))
    await act(async () => { await result.current.start() })
    expect(result.current.error).toMatch(/not supported/)
  })
})
