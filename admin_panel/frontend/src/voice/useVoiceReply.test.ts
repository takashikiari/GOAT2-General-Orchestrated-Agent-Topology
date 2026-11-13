import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const earcon = { prime: vi.fn(), start: vi.fn(), stop: vi.fn(), close: vi.fn(), playing: false }
const spoken: string[] = []
let finish: (r: 'ended' | 'unavailable') => void = () => {}
const remote = {
  prime: vi.fn(),
  close: vi.fn(),
  speak: vi.fn((text: string) => {
    spoken.push(text)
    return { done: new Promise<'ended' | 'unavailable'>((resolve) => { finish = resolve }), cancel: vi.fn() }
  }),
}
vi.mock('./earcon', () => ({ createEarcon: () => earcon }))
vi.mock('./remoteVoice', () => ({ createRemoteVoice: () => remote, remoteVoiceAvailable: () => true }))

import { useVoiceReply } from './useVoiceReply'

type T = { id: string; status: string; reply: string | null }
const setup = (turns: T[]) => renderHook(({ t }) => useVoiceReply(t), { initialProps: { t: turns } })

beforeEach(() => {
  spoken.length = 0
  vi.clearAllMocks()
})
afterEach(() => vi.restoreAllMocks())

describe('useVoiceReply', () => {
  it('plays the working sound from send until the reply is read, then reads it once with the fixed voice', async () => {
    const h = setup([])
    act(() => h.result.current.prime())
    expect(earcon.prime).toHaveBeenCalled()
    expect(remote.prime).toHaveBeenCalled()
    act(() => h.result.current.expect('t1'))
    expect(earcon.start).toHaveBeenCalledTimes(1)
    expect(h.result.current.phase).toBe('thinking')

    h.rerender({ t: [{ id: 't1', status: 'running', reply: null }] })
    expect(earcon.stop).not.toHaveBeenCalled()
    expect(spoken).toEqual([])

    h.rerender({ t: [{ id: 't1', status: 'done', reply: 'Ai **două** sarcini azi.' }] })
    expect(earcon.stop).toHaveBeenCalled()
    expect(spoken).toEqual(['Ai două sarcini azi.']) // markdown stripped before speaking
    expect(h.result.current.phase).toBe('speaking')
    h.rerender({ t: [{ id: 't1', status: 'done', reply: 'Ai **două** sarcini azi.' }] })
    expect(spoken).toHaveLength(1) // not read twice on the next poll
    await act(async () => { finish('ended') })
    expect(h.result.current.phase).toBe('idle')
  })

  it('stops the working sound and reads nothing when the turn fails or is cancelled', () => {
    const h = setup([])
    act(() => { h.result.current.prime(); h.result.current.expect('t2') })
    h.rerender({ t: [{ id: 't2', status: 'error', reply: null }] })
    expect(earcon.stop).toHaveBeenCalled()
    expect(spoken).toEqual([])
    expect(h.result.current.phase).toBe('idle')
  })

  it('ignores other turns (typed messages are not read aloud)', () => {
    const h = setup([])
    act(() => { h.result.current.prime(); h.result.current.expect('t3') })
    h.rerender({ t: [{ id: 'typed', status: 'done', reply: 'text' }] })
    expect(spoken).toEqual([])
    expect(earcon.stop).not.toHaveBeenCalled()
  })

  it('stop() silences everything', () => {
    const h = setup([])
    act(() => { h.result.current.prime(); h.result.current.expect('t4'); h.result.current.stop() })
    expect(earcon.stop).toHaveBeenCalled()
    h.rerender({ t: [{ id: 't4', status: 'done', reply: 'gata' }] })
    expect(spoken).toEqual([]) // the reply of a stopped message is not read
  })
})
