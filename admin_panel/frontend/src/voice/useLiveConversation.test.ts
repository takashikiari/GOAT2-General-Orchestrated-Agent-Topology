import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeMedia } from '../test/fakeMedia'

const capture = vi.hoisted(() => ({
  started: 0,
  stops: 0,
  paused: false,
  gain: 0,
  onFrame: null as null | ((pcm: ArrayBuffer) => void),
  fail: null as null | Error,
}))
vi.mock('./pcmCapture', () => ({
  startPcmCapture: async (_stream: unknown, onFrame: (pcm: ArrayBuffer) => void, opts?: { gain?: number }) => {
    if (capture.fail) throw capture.fail
    Object.assign(capture, { started: capture.started + 1, gain: opts?.gain ?? 1 })
    capture.onFrame = onFrame
    return { setPaused: (p: boolean) => { capture.paused = p }, stop: () => { capture.stops++ } }
  },
}))

const playerState = vi.hoisted(() => ({
  available: true,
  onPlaying: null as null | ((p: boolean) => void),
  pushed: [] as ArrayBuffer[],
  rate: 0,
  primed: 0,
  cleared: 0,
  closed: 0,
}))
vi.mock('./pcmPlayer', () => ({
  pcmPlayerAvailable: () => playerState.available,
  createPcmPlayer: (onPlaying: (p: boolean) => void) => {
    playerState.onPlaying = onPlaying
    return {
      prime: () => { playerState.primed++ },
      setRate: (r: number) => { playerState.rate = r },
      push: (b: ArrayBuffer) => { playerState.pushed.push(b) },
      clear: () => { playerState.cleared++; playerState.onPlaying?.(false) },
      close: () => { playerState.closed++ },
    }
  },
}))

import { LIVE_MIC_GAIN, TAP_WINDOW_MS, useLiveConversation } from './useLiveConversation'

class FakeSocket {
  static last: FakeSocket
  binaryType = ''
  readyState = 0
  sent: (string | ArrayBuffer)[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeSocket.last = this
  }
  send(data: string | ArrayBuffer) { this.sent.push(data) }
  close() { this.readyState = 3 }
  open() { this.readyState = 1; this.onopen?.() }
  json(msg: unknown) { this.onmessage?.({ data: JSON.stringify(msg) }) }
  bytes(buf: ArrayBuffer) { this.onmessage?.({ data: buf }) }
}
const sentTypes = () => FakeSocket.last.sent.filter((m) => typeof m === 'string').map((m) => JSON.parse(m as string).type)
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

let media: ReturnType<typeof installFakeMedia>

async function started() {
  const hook = renderHook(() => useLiveConversation())
  await act(async () => { await hook.result.current.start() })
  act(() => FakeSocket.last.open())
  act(() => FakeSocket.last.json({ type: 'ready', in_rate: 16000, out_rate: 24000 }))
  await flush()
  return hook
}

beforeEach(() => {
  Object.assign(capture, { started: 0, stops: 0, paused: false, onFrame: null, fail: null })
  Object.assign(playerState, { available: true, onPlaying: null, pushed: [], rate: 0, primed: 0, cleared: 0, closed: 0 })
  media = installFakeMedia()
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useLiveConversation', () => {
  it('asks for the microphone, opens the socket with the Telegram session, and listens once the server is ready', async () => {
    const hook = renderHook(() => useLiveConversation())
    await act(async () => { await hook.result.current.start() })
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(playerState.primed).toBe(1)
    expect(FakeSocket.last.url).toMatch(/^wss?:\/\/.*\/api\/voice\/live$/)
    expect(hook.result.current.phase).toBe('connecting')
    act(() => FakeSocket.last.open())
    expect(JSON.parse(FakeSocket.last.sent[0] as string)).toMatchObject({ type: 'auth' })
    act(() => FakeSocket.last.json({ type: 'ready', in_rate: 16000, out_rate: 24000 }))
    await flush()
    expect(hook.result.current.active).toBe(true)
    expect(hook.result.current.phase).toBe('listening')
    expect(playerState.rate).toBe(24000)
    capture.onFrame?.(new ArrayBuffer(8000)) // the microphone's audio goes to the server
    expect(FakeSocket.last.sent[FakeSocket.last.sent.length - 1]).toBeInstanceOf(ArrayBuffer)
  })

  it('shows what was heard and which tool GOAT is using, and "thinking" while it works', async () => {
    const hook = await started()
    act(() => FakeSocket.last.json({ type: 'user_text', text: 'verifică logurile', final: false }))
    expect(hook.result.current.interim).toBe('verifică logurile')
    act(() => FakeSocket.last.json({ type: 'state', state: 'thinking' }))
    act(() => FakeSocket.last.json({ type: 'tool', name: 'get_recent_logs', status: 'running' }))
    expect(hook.result.current.phase).toBe('thinking')
    expect(hook.result.current.activity).toBe('get_recent_logs')
    act(() => FakeSocket.last.json({ type: 'tool', name: 'get_recent_logs', status: 'done' }))
    expect(hook.result.current.activity).toBeNull()
  })

  it('keeps one microphone open the whole conversation and sends nothing while the model\'s voice plays', async () => {
    const hook = await started()
    const frames = () => FakeSocket.last.sent.filter((m) => m instanceof ArrayBuffer).length
    act(() => FakeSocket.last.bytes(new ArrayBuffer(960)))
    act(() => playerState.onPlaying?.(true))
    expect([playerState.pushed.length, hook.result.current.phase, capture.paused]).toEqual([1, 'speaking', true])
    const before = frames()
    capture.onFrame?.(new ArrayBuffer(8000)) // even a frame already on its way is not sent
    expect(frames()).toBe(before)
    act(() => playerState.onPlaying?.(false))
    capture.onFrame?.(new ArrayBuffer(8000))
    expect([frames(), capture.paused, hook.result.current.phase]).toEqual([before + 1, false, 'listening'])
    // never closed and never asked for again: no new permission prompt after a reply
    expect([media.getUserMedia.mock.calls.length, capture.started, capture.stops, capture.gain]).toEqual([1, 1, 0, LIVE_MIC_GAIN])
    expect(media.track.stop).not.toHaveBeenCalled()
  })

  it('tells the server when the microphone is open or closed', async () => {
    const hook = await started()
    const mic = () => FakeSocket.last.sent.filter((m) => typeof m === 'string').map((m) => JSON.parse(m as string)).filter((m) => m.type === 'mic').map((m) => m.on)
    expect(mic()).toEqual([true]) // opened before the socket was ready, told once it was
    act(() => playerState.onPlaying?.(true)) // the model's voice plays: nothing is sent
    expect(mic()).toEqual([true, false])
    act(() => playerState.onPlaying?.(false))
    await flush()
    expect(mic()).toEqual([true, false, true])
    act(() => hook.result.current.toggleMute())
    expect(mic()).toEqual([true, false, true, false])
  })

  it('an interrupted that answers our own tap does not cut off the audio that came after it', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    const hook = await started()
    act(() => hook.result.current.interrupt()) // the tap clears by itself
    expect(playerState.cleared).toBe(1)
    act(() => FakeSocket.last.bytes(new ArrayBuffer(960))) // newer speech
    act(() => FakeSocket.last.json({ type: 'interrupted' })) // the late answer to the tap
    expect(playerState.cleared).toBe(1) // not cleared again
    vi.setSystemTime(Date.now() + TAP_WINDOW_MS + 10)
    act(() => FakeSocket.last.json({ type: 'interrupted' })) // a real cut-off later
    expect(playerState.cleared).toBe(2)
  })

  it('forgets the words that were heard once the answer to them has been spoken', async () => {
    const hook = await started()
    act(() => FakeSocket.last.json({ type: 'user_text', text: 'verifică logurile', final: true }))
    expect(hook.result.current.interim).toBe('verifică logurile')
    act(() => playerState.onPlaying?.(true))
    expect(hook.result.current.interim).toBe('verifică logurile') // still shown while the answer plays
    act(() => playerState.onPlaying?.(false))
    expect(hook.result.current.interim).toBe('')
  })

  it('a tap cuts the model off, tells the server, and listens again', async () => {
    const hook = await started()
    act(() => playerState.onPlaying?.(true))
    act(() => hook.result.current.interrupt())
    await flush()
    expect(sentTypes()).toContain('interrupt')
    expect(playerState.cleared).toBe(1)
    expect(hook.result.current.phase).toBe('listening')
    expect([capture.started, capture.paused]).toEqual([1, false]) // the same microphone, sending again
  })

  it('the server cutting the model off (interrupted) clears what was queued', async () => {
    await started()
    act(() => FakeSocket.last.json({ type: 'interrupted' }))
    expect(playerState.cleared).toBe(1)
  })

  it('mute stops sending and keeps it stopped after the voice stops, until it is unmuted', async () => {
    const hook = await started()
    act(() => hook.result.current.toggleMute())
    expect(hook.result.current.muted).toBe(true)
    expect([capture.paused, capture.stops]).toEqual([true, 0])
    act(() => playerState.onPlaying?.(true))
    act(() => playerState.onPlaying?.(false))
    expect(capture.paused).toBe(true) // still muted
    act(() => hook.result.current.toggleMute())
    expect([capture.paused, capture.started, media.getUserMedia.mock.calls.length]).toEqual([false, 1, 1])
  })

  it('end tells the server, closes everything and releases the microphone', async () => {
    const hook = await started()
    act(() => hook.result.current.end())
    expect(hook.result.current.active).toBe(false)
    expect(sentTypes()).toContain('close')
    expect(FakeSocket.last.readyState).toBe(3)
    expect(playerState.closed).toBe(1)
    expect(capture.stops).toBe(1)
  })

  it('the normal ends (silence, time limit) are notices; real failures are errors; both end the conversation', async () => {
    const quiet = await started()
    act(() => FakeSocket.last.json({ type: 'error', code: 'idle', message: 'x' }))
    expect(quiet.result.current.active).toBe(false)
    expect(quiet.result.current.notice).toMatch(/silence/)
    expect([quiet.result.current.error, quiet.result.current.providerDown]).toEqual([null, false]) // nothing to fall back from
    const broken = await started()
    act(() => FakeSocket.last.json({ type: 'error', code: 'not_configured', message: 'x' }))
    expect(broken.result.current.active).toBe(false)
    expect(broken.result.current.error).toMatch(/not set up/)
    expect(broken.result.current.providerDown).toBe(true)
  })

  it('a denied microphone ends it at once, without opening a socket', async () => {
    media = installFakeMedia({ getUserMedia: async () => { throw Object.assign(new Error('no'), { name: 'NotAllowedError' }) } })
    const hook = renderHook(() => useLiveConversation())
    const sockets = FakeSocket.last
    await act(async () => { await hook.result.current.start() })
    expect([hook.result.current.active, /permission denied/i.test(hook.result.current.error ?? '')]).toEqual([false, true])
    expect(hook.result.current.providerDown).toBe(false) // the microphone, not the live voice: Deepgram would fail too
    expect(FakeSocket.last).toBe(sockets)
  })

  it('a socket that drops by itself ends the conversation with an error', async () => {
    const hook = await started()
    act(() => FakeSocket.last.onclose?.())
    expect(hook.result.current.active).toBe(false)
    expect(hook.result.current.error).toMatch(/closed/)
    expect(hook.result.current.providerDown).toBe(true)
  })

  it('a go-away notice from the server is shown but does not end the conversation', async () => {
    const hook = await started()
    act(() => FakeSocket.last.json({ type: 'notice', code: 'go_away', seconds: 5 }))
    expect(hook.result.current.notice).toMatch(/disconnect soon/)
    expect(hook.result.current.active).toBe(true)
  })

  it('says so on a browser that cannot do it', async () => {
    playerState.available = false
    const hook = renderHook(() => useLiveConversation())
    await act(async () => { await hook.result.current.start() })
    expect(hook.result.current.active).toBe(false)
    expect(hook.result.current.error).toMatch(/cannot hold a live voice/)
  })
})
