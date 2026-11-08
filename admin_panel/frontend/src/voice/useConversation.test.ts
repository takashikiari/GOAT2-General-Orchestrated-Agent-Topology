import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const stream = vi.hoisted(() => ({
  handlers: null as null | { onUtterance: (t: string) => void; onActivity?: () => void },
  state: 'idle' as 'idle' | 'connecting' | 'listening',
  error: null as string | null,
  interim: '',
  start: vi.fn(),
  stop: vi.fn(),
  setPaused: vi.fn(),
}))
vi.mock('./useVoiceStream', () => ({
  useVoiceStream: (handlers: typeof stream.handlers) => {
    stream.handlers = handlers
    return { state: stream.state, interim: stream.interim, error: stream.error, start: stream.start, stop: stream.stop, setPaused: stream.setPaused }
  },
}))

const speech = vi.hoisted(() => ({
  available: true,
  voice: { lang: 'ro-RO', name: 'Ioana' } as unknown,
  spoken: [] as string[],
  finish: null as null | (() => void),
  cancel: vi.fn(),
}))
vi.mock('./speech', async (importActual) => {
  const actual = await importActual<typeof import('./speech')>()
  return {
    ...actual,
    speechAvailable: () => speech.available,
    pickVoice: async () => speech.voice,
    speak: (text: string) => {
      speech.spoken.push(text)
      let resolve: () => void = () => {}
      const done = new Promise<void>((r) => { resolve = r })
      speech.finish = resolve
      return { done, cancel: () => { speech.cancel(); resolve() } }
    },
  }
})

const remote = vi.hoisted(() => ({
  available: false,
  spoken: [] as string[],
  primed: 0,
  created: 0,
  closed: 0,
  finish: null as null | ((r: 'ended' | 'unavailable') => void),
  cancel: vi.fn(),
}))
vi.mock('./remoteVoice', () => ({
  remoteVoiceAvailable: () => remote.available,
  createRemoteVoice: () => {
    remote.created++
    return {
      close: () => { remote.closed++ },
      prime: () => { remote.primed++ },
      speak: (text: string) => {
        remote.spoken.push(text)
        let resolve: (r: 'ended' | 'unavailable') => void = () => {}
        const done = new Promise<'ended' | 'unavailable'>((r) => { resolve = r })
        remote.finish = resolve
        return { done, cancel: () => { remote.cancel(); resolve('ended') } }
      },
    }
  },
}))

import { IDLE_MS, useConversation } from './useConversation'

type Turn = { id: string; status: string; reply: string | null }
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })

async function open(submit = vi.fn(async (_t: string) => 't1' as string | null)) {
  const hook = renderHook(({ turns }: { turns: Turn[] }) => useConversation({ turns, submit }), { initialProps: { turns: [] as Turn[] } })
  act(() => hook.result.current.start())
  stream.state = 'listening'
  hook.rerender({ turns: [] })
  await flush()
  return { ...hook, submit }
}
const say = async (text: string) => { act(() => stream.handlers!.onUtterance(text)); await flush() }

beforeEach(() => {
  vi.useFakeTimers()
  Object.assign(stream, { handlers: null, state: 'idle', error: null, interim: '' })
  stream.start.mockClear(); stream.stop.mockClear(); stream.setPaused.mockClear()
  Object.assign(speech, { available: true, voice: { lang: 'ro-RO', name: 'Ioana' }, spoken: [], finish: null })
  speech.cancel.mockClear()
  Object.assign(remote, { available: false, spoken: [], primed: 0, created: 0, closed: 0, finish: null })
  remote.cancel.mockClear()
  window.localStorage.clear()
})
afterEach(() => vi.useRealTimers())

describe('useConversation', () => {
  it('starts the stream, shows "connecting", then "listening" once the stream is up', async () => {
    const { result } = await open()
    expect(stream.start).toHaveBeenCalledTimes(1)
    expect(result.current.active).toBe(true)
    expect(result.current.phase).toBe('listening')
  })

  it('what is said goes to GOAT as a message, with the microphone paused while it thinks', async () => {
    const { result, submit } = await open()
    await say('verifică logurile')
    expect(submit).toHaveBeenCalledWith('verifică logurile')
    expect(result.current.phase).toBe('thinking')
    expect(stream.setPaused).toHaveBeenLastCalledWith(true)
  })

  it('reads the reply aloud, then listens again', async () => {
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'running', reply: null }] })
    await flush()
    expect(result.current.phase).toBe('thinking')
    rerender({ turns: [{ id: 't1', status: 'done', reply: '**Gata:** totul e în regulă. Vezi [raportul](https://x.example).' }] })
    await flush()
    expect(result.current.phase).toBe('speaking')
    expect(speech.spoken).toEqual(['Gata: totul e în regulă. Vezi raportul.'])
    await act(async () => { speech.finish?.() })
    await flush()
    expect(result.current.phase).toBe('listening')
    expect(stream.setPaused).toHaveBeenLastCalledWith(false)
  })

  it('goes back to listening without speaking when the turn fails or is stopped', async () => {
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'error', reply: null }] })
    await flush()
    expect(result.current.phase).toBe('listening')
    expect(speech.spoken).toEqual([])
  })

  it('tapping to interrupt stops the speech and listens again', async () => {
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'done', reply: 'Un răspuns destul de lung. Încă o propoziție.' }] })
    await flush()
    expect(result.current.phase).toBe('speaking')
    act(() => result.current.interrupt())
    await flush()
    expect(speech.cancel).toHaveBeenCalled()
    expect(result.current.phase).toBe('listening')
  })

  it('does not speak when the device has no Romanian voice, and says so once', async () => {
    speech.voice = null
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'done', reply: 'Răspuns.' }] })
    await flush()
    expect(speech.spoken).toEqual([])
    expect(result.current.phase).toBe('listening')
    expect(result.current.notice).toMatch(/No Romanian voice/)
  })

  it('does not speak in a browser without speech synthesis', async () => {
    speech.available = false
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'done', reply: 'Răspuns.' }] })
    await flush()
    expect(result.current.notice).toMatch(/cannot speak/)
    expect(result.current.phase).toBe('listening')
  })

  it('does nothing for a reply that is only code, and keeps listening', async () => {
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'done', reply: '```\ncod\n```' }] })
    await flush()
    expect(speech.spoken).toEqual([])
    expect(result.current.phase).toBe('listening')
  })

  it('a message that was not accepted goes straight back to listening', async () => {
    const { result } = await open(vi.fn(async () => null))
    await say('salut')
    expect(result.current.phase).toBe('listening')
  })

  it('a message whose sending failed goes back to listening with a notice', async () => {
    const { result } = await open(vi.fn(async () => { throw new Error('x') }))
    await say('salut')
    await flush()
    expect(result.current.phase).toBe('listening')
    expect(result.current.notice).toMatch(/could not be sent/)
  })

  it('mute stops listening without ending, and an utterance heard while muted is ignored', async () => {
    const { result, submit } = await open()
    act(() => result.current.toggleMute())
    expect(result.current.muted).toBe(true)
    expect(stream.setPaused).toHaveBeenLastCalledWith(true)
    await say('ignorat')
    expect(submit).not.toHaveBeenCalled()
    act(() => result.current.toggleMute())
    expect(stream.setPaused).toHaveBeenLastCalledWith(false)
  })

  it('ends by itself after a stretch of silence', async () => {
    const { result } = await open()
    await act(async () => { await vi.advanceTimersByTimeAsync(IDLE_MS + 100) })
    expect(result.current.active).toBe(false)
    expect(stream.stop).toHaveBeenCalled()
    expect(result.current.notice).toMatch(/after 90 seconds of silence/)
  })

  it('speech resets the silence timer', async () => {
    const { result } = await open()
    await act(async () => { await vi.advanceTimersByTimeAsync(IDLE_MS - 1000) })
    act(() => stream.handlers!.onActivity?.())
    await act(async () => { await vi.advanceTimersByTimeAsync(IDLE_MS - 1000) })
    expect(result.current.active).toBe(true)
  })

  it('end stops everything: the stream, the speech, the timers', async () => {
    const { result, rerender } = await open()
    await say('salut')
    rerender({ turns: [{ id: 't1', status: 'done', reply: 'Un răspuns.' }] })
    await flush()
    act(() => result.current.end())
    expect(result.current.active).toBe(false)
    expect(stream.stop).toHaveBeenCalled()
    expect(speech.cancel).toHaveBeenCalled()
  })

  it('a stream failure ends the conversation and its message stays available', async () => {
    const { result, rerender } = await open()
    stream.error = 'Microphone permission denied.'
    rerender({ turns: [] })
    await flush()
    expect(result.current.active).toBe(false)
    expect(result.current.error).toMatch(/permission denied/i)
  })

  describe('with the Deepgram voice', () => {
    const reply = async (text: string) => {
      const hook = await open()
      await say('salut')
      hook.rerender({ turns: [{ id: 't1', status: 'done', reply: text }] })
      await flush()
      return hook
    }

    it('reads the reply with Deepgram (not the browser voice), primed by the tap that started the conversation', async () => {
      remote.available = true
      const { result } = await reply('Gata: totul e în regulă.')
      expect(remote.primed).toBe(1)
      expect(remote.spoken).toEqual(['Gata: totul e în regulă.'])
      expect(speech.spoken).toEqual([])
      expect(result.current.phase).toBe('speaking')
      await act(async () => { remote.finish?.('ended') })
      await flush()
      expect(result.current.phase).toBe('listening')
    })

    it('one voice (one audio context) serves every conversation, and it is released when the page goes', async () => {
      remote.available = true
      const { result, unmount } = await open()
      act(() => result.current.end())
      act(() => result.current.start())
      act(() => result.current.end())
      act(() => result.current.start())
      expect(remote.created).toBe(1)
      expect(remote.primed).toBe(3)
      unmount()
      expect(remote.closed).toBe(1)
    })

    it('tapping to interrupt stops it and listens again', async () => {
      remote.available = true
      const { result } = await reply('Un răspuns destul de lung. Încă o propoziție.')
      act(() => result.current.interrupt())
      await flush()
      expect(remote.cancel).toHaveBeenCalled()
      expect(result.current.phase).toBe('listening')
    })

    it('falls back to the browser voice, once, with a notice, when Deepgram cannot speak', async () => {
      remote.available = true
      const ids = ['t1', 't2']
      const hook = await open(vi.fn(async (_t: string) => ids.shift() as string))
      await say('salut')
      hook.rerender({ turns: [{ id: 't1', status: 'done', reply: 'Primul răspuns.' }] })
      await flush()
      await act(async () => { remote.finish?.('unavailable') })
      await flush()
      expect(hook.result.current.notice).toMatch(/Deepgram voice is not available/)
      expect(speech.spoken).toEqual(['Primul răspuns.'])
      await act(async () => { speech.finish?.() })
      await flush()
      expect(hook.result.current.phase).toBe('listening')
      // the next reply goes straight to the browser voice: Deepgram is not asked again in this conversation
      await say('încă ceva')
      hook.rerender({ turns: [{ id: 't1', status: 'done', reply: 'Primul răspuns.' }, { id: 't2', status: 'done', reply: 'Al doilea răspuns.' }] })
      await flush()
      expect(remote.spoken).toEqual(['Primul răspuns.'])
      expect(speech.spoken).toEqual(['Primul răspuns.', 'Al doilea răspuns.'])
    })

    it('says both things when Deepgram failed and the device has no voice to fall back on', async () => {
      remote.available = true
      speech.voice = null
      const { result } = await reply('Răspuns.')
      await act(async () => { remote.finish?.('unavailable') })
      await flush()
      expect(result.current.notice).toMatch(/Deepgram voice is not available right now and no Romanian voice/)
      expect(result.current.phase).toBe('listening')
    })

    it('uses the browser voice when the device cannot play Deepgram audio', async () => {
      remote.available = false
      await reply('Răspuns.')
      expect(speech.spoken).toEqual(['Răspuns.'])
    })

    it('the voice choice is remembered and decides who speaks', async () => {
      remote.available = true
      const first = await open()
      expect(first.result.current.voiceKind).toBe('deepgram')
      act(() => first.result.current.setVoiceKind('browser'))
      expect(first.result.current.voiceKind).toBe('browser')
      expect(window.localStorage.getItem('goat.voice')).toBe('browser')
      await say('salut')
      first.rerender({ turns: [{ id: 't1', status: 'done', reply: 'Cu vocea browserului.' }] })
      await flush()
      expect(speech.spoken).toEqual(['Cu vocea browserului.'])
      expect(remote.spoken).toEqual([])
      first.unmount()
      const again = renderHook(() => useConversation({ turns: [], submit: async () => null }))
      expect(again.result.current.voiceKind).toBe('browser')
    })
  })
})
