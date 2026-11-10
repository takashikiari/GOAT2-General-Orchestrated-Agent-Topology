import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'

const classic = vi.hoisted(() => ({
  value: null as unknown as Record<string, unknown>,
  setVoiceKind: vi.fn(),
}))
const live = vi.hoisted(() => ({ value: null as unknown as Record<string, unknown> }))
vi.mock('./useConversation', () => ({ useConversation: () => classic.value }))
vi.mock('./useLiveConversation', () => ({ useLiveConversation: () => live.value }))

import { useVoiceConversation } from './useVoiceConversation'

const base = (over: Record<string, unknown> = {}) => ({
  active: false, phase: 'listening', muted: false, interim: '', error: null, notice: null, elapsedMs: 0, providerDown: false,
  start: vi.fn(), end: vi.fn(), toggleMute: vi.fn(), interrupt: vi.fn(), ...over,
})
const args = { turns: [], submit: async () => null }
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => {
  classic.setVoiceKind.mockClear()
  classic.value = { ...base(), voiceKind: 'deepgram', setVoiceKind: classic.setVoiceKind }
  live.value = { ...base({ activity: 'get_recent_logs' }) }
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('useVoiceConversation', () => {
  it('is the classic conversation when the server has no live voice (and offers only Deepgram / browser)', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: false } as never)
    const { result } = renderHook(() => useVoiceConversation(args))
    await flush()
    expect(result.current.voiceMode).toBe('deepgram')
    expect(result.current.start).toBe((classic.value as { start: unknown }).start)
    act(() => result.current.cycleVoice())
    expect(classic.setVoiceKind).toHaveBeenCalledWith('browser')
  })

  it('offers Live when the server has it: live → deepgram → browser → live, and remembers Live', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: true } as never)
    window.localStorage.setItem('goat.voice', 'live')
    const { result, rerender } = renderHook(() => useVoiceConversation(args))
    await flush()
    expect(result.current.voiceMode).toBe('live')
    expect(result.current.activity).toBe('get_recent_logs')
    expect(result.current.start).toBe((live.value as { start: unknown }).start)
    act(() => result.current.cycleVoice())
    expect(classic.setVoiceKind).toHaveBeenLastCalledWith('deepgram')
    expect(result.current.voiceMode).toBe('deepgram')
    act(() => result.current.cycleVoice())
    expect(classic.setVoiceKind).toHaveBeenLastCalledWith('browser')
    ;(classic.value as { voiceKind: string }).voiceKind = 'browser' // what the real hook does after setVoiceKind
    rerender()
    expect(result.current.voiceMode).toBe('browser')
    act(() => result.current.cycleVoice())
    expect(window.localStorage.getItem('goat.voice')).toBe('live')
    expect(result.current.voiceMode).toBe('live')
  })

  it('a remembered "live" is ignored when the server does not have it any more', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: false } as never)
    window.localStorage.setItem('goat.voice', 'live')
    const { result } = renderHook(() => useVoiceConversation(args))
    await flush()
    expect(result.current.voiceMode).toBe('deepgram')
  })

  it('does not switch voices while a conversation is running, and survives a failing status request', async () => {
    vi.spyOn(client, 'apiGet').mockRejectedValue(new Error('down'))
    classic.value = { ...(classic.value as object), active: true }
    const { result } = renderHook(() => useVoiceConversation(args))
    await flush()
    act(() => result.current.cycleVoice())
    expect(classic.setVoiceKind).not.toHaveBeenCalled()
    expect(result.current.active).toBe(true)
  })

  it('when the live voice fails, the conversation carries on with the Deepgram voice and says why', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: true, limit_reached: false } as never)
    window.localStorage.setItem('goat.voice', 'live')
    const { result, rerender } = renderHook(() => useVoiceConversation(args))
    await flush()
    expect(result.current.voiceMode).toBe('live')
    live.value = { ...(live.value as object), providerDown: true, error: 'The live voice service is not reachable right now.' }
    rerender()
    await flush()
    expect(result.current.voiceMode).toBe('deepgram')
    expect((classic.value as { start: ReturnType<typeof vi.fn> }).start).toHaveBeenCalledTimes(1)
    expect(result.current.notice).toMatch(/not reachable.*Deepgram voice/)
    expect(result.current.error).toBeNull() // the classic conversation's own error, not the live one
    expect(window.localStorage.getItem('goat.voice')).toBe('live') // tried again next time the page opens
    act(() => result.current.cycleVoice()) // deepgram → browser → live: choosing Live again retries it
    ;(classic.value as { voiceKind: string }).voiceKind = 'browser' // what the real hook does after setVoiceKind
    rerender()
    act(() => result.current.cycleVoice())
    expect(result.current.voiceMode).toBe('live') // and the old failure does not send it straight back to Deepgram
    expect((classic.value as { start: ReturnType<typeof vi.fn> }).start).toHaveBeenCalledTimes(1)
  })

  it('does not fall back on its own when a conversation is already running in the classic voice', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: true } as never)
    window.localStorage.setItem('goat.voice', 'live')
    classic.value = { ...(classic.value as object), active: true }
    live.value = { ...(live.value as object), providerDown: true }
    renderHook(() => useVoiceConversation(args))
    await flush()
    expect((classic.value as { start: ReturnType<typeof vi.fn> }).start).not.toHaveBeenCalled()
  })

  it('once the day\'s live minutes are used up, the Deepgram voice is used and Live is not offered', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ available: true, limit_reached: true } as never)
    window.localStorage.setItem('goat.voice', 'live')
    const { result } = renderHook(() => useVoiceConversation(args))
    await flush()
    expect(result.current.voiceMode).toBe('deepgram')
    act(() => result.current.cycleVoice())
    expect(classic.setVoiceKind).toHaveBeenLastCalledWith('browser')
  })
})
