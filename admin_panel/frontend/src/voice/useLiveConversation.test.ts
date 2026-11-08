import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installFakeMedia } from '../test/fakeMedia'

const capture = vi.hoisted(() => ({
  started: 0,
  stops: 0,
  onFrame: null as null | ((pcm: ArrayBuffer) => void),
  fail: null as null | Error,
}))
vi.mock('./pcmCapture', () => ({
  startPcmCapture: async (_stream: unknown, onFrame: (pcm: ArrayBuffer) => void) => {
    if (capture.fail) throw capture.fail
    capture.started++
    capture.onFrame = onFrame
    return { setPaused: vi.fn(), stop: () => { capture.stops++ } }
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

import { TAP_WINDOW_MS, useLiveConversation } from './useLiveConversation'

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
  Object.assign(capture, { started: 0, stops: 0, onFrame: null, fail: null })
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

  it('plays the model\'s voice and releases the microphone meanwhile, then takes it back', async () => {
    const hook = await started()
    const before = media.getUserMedia.mock.calls.length
    act(() => FakeSocket.last.bytes(new ArrayBuffer(960)))
    expect(playerState.pushed.length).toBe(1)
    act(() => playerState.onPlaying?.(true))
    expect(hook.result.current.phase).toBe('speaking')
    expect(capture.stops).toBe(1)
    expect(media.track.stop).toHaveBeenCalled()
    act(() => playerState.onPlaying?.(false))
    await flush()
    expect(media.getUserMedia.mock.calls.length).toBe(before + 1)
    expect(capture.started).toBe(2)
    expect(hook.result.current.phase).toBe('listening')
  })

  it('tells the server when the microphone is open or closed', async () => {
    const hook = await started()
    const mic = () => FakeSocket.last.sent.filter((m) => typeof m === 'string').map((m) => JSON.parse(m as string)).filter((m) => m.type === 'mic').map((m) => m.on)
    expect(mic()).toEqual([true]) // opened before the socket was ready, told once it was
    act(() => playerState.onPlaying?.(true)) // the model's voice plays: the microphone is released
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
    expect(capture.started).toBe(2)
  })

  it('the server cutting the model off (interrupted) clears what was queued', async () => {
    await started()
    act(() => FakeSocket.last.json({ type: 'interrupted' }))
    expect(playerState.cleared).toBe(1)
  })

  it('mute releases the microphone and keeps it released after the voice stops, until it is unmuted', async () => {
    const hook = await started()
    act(() => hook.result.current.toggleMute())
    expect(hook.result.current.muted).toBe(true)
    expect(capture.stops).toBe(1)
    act(() => playerState.onPlaying?.(true))
    act(() => playerState.onPlaying?.(false))
    await flush()
    expect(capture.started).toBe(1) // not reopened while muted
    act(() => hook.result.current.toggleMute())
    await flush()
    expect(capture.started).toBe(2)
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
    expect(quiet.result.current.error).toBeNull()
    const broken = await started()
    act(() => FakeSocket.last.json({ type: 'error', code: 'not_configured', message: 'x' }))
    expect(broken.result.current.active).toBe(false)
    expect(broken.result.current.error).toMatch(/not set up/)
  })

  it('a denied microphone ends it at once, without opening a socket', async () => {
    media = installFakeMedia({ getUserMedia: async () => { throw Object.assign(new Error('no'), { name: 'NotAllowedError' }) } })
    const hook = renderHook(() => useLiveConversation())
    const sockets = FakeSocket.last
    await act(async () => { await hook.result.current.start() })
    expect(hook.result.current.active).toBe(false)
    expect(hook.result.current.error).toMatch(/permission denied/i)
    expect(FakeSocket.last).toBe(sockets)
  })

  it('a socket that drops by itself ends the conversation with an error', async () => {
    const hook = await started()
    act(() => FakeSocket.last.onclose?.())
    expect(hook.result.current.active).toBe(false)
    expect(hook.result.current.error).toMatch(/closed/)
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
