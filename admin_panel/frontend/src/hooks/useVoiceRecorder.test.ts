import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeRecorder, installFakeMedia, removeFakeMedia } from '../test/fakeMedia'
import { VOICE_MAX_MS, describeMicError, pickMimeType, useVoiceRecorder } from './useVoiceRecorder'

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('pickMimeType', () => {
  it('prefers webm/opus, falls back to mp4 where webm cannot be recorded (Safari/iOS)', () => {
    installFakeMedia()
    expect(pickMimeType()).toBe('audio/webm;codecs=opus')
    FakeRecorder.supported = new Set(['audio/mp4'])
    expect(pickMimeType()).toBe('audio/mp4')
    FakeRecorder.supported = new Set()
    expect(pickMimeType()).toBeUndefined()
  })
})

describe('useVoiceRecorder', () => {
  it('records on the first tap and hands the clip over on the second', async () => {
    const media = installFakeMedia()
    const onRecorded = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    expect(result.current.state).toBe('idle')
    act(() => result.current.toggle())
    await flush()
    expect(media.getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(result.current.state).toBe('recording')
    await advance(2100)
    expect(result.current.elapsedMs).toBeGreaterThanOrEqual(2000)
    act(() => result.current.toggle())
    await flush()
    expect(onRecorded).toHaveBeenCalledTimes(1)
    const clip = onRecorded.mock.calls[0][0]
    expect(clip.mimeType).toBe('audio/webm;codecs=opus')
    expect(clip.blob.size).toBe(2048)
    expect(clip.durationMs).toBeGreaterThanOrEqual(2000)
    expect(clip.limitReached).toBe(false)
    expect(result.current.state).toBe('idle')
    expect(media.track.stop).toHaveBeenCalled() // the microphone is released
  })

  it('shows "sending" until the upload settles', async () => {
    installFakeMedia()
    let finish: () => void = () => {}
    const onRecorded = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(1000)
    act(() => result.current.toggle())
    await flush()
    expect(result.current.state).toBe('sending')
    act(() => result.current.toggle()) // ignored while sending
    expect(result.current.state).toBe('sending')
    await act(async () => { finish() })
    expect(result.current.state).toBe('idle')
  })

  it('uses the mp4 fallback when webm is not available', async () => {
    installFakeMedia()
    FakeRecorder.supported = new Set(['audio/mp4'])
    const onRecorded = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(1000)
    act(() => result.current.toggle())
    await flush()
    expect(FakeRecorder.instances[0].options?.mimeType).toBe('audio/mp4')
    expect(onRecorded.mock.calls[0][0].mimeType).toBe('audio/mp4')
  })

  it('stops by itself at the 60 s limit and says so', async () => {
    const media = installFakeMedia()
    const onRecorded = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(VOICE_MAX_MS + 100)
    expect(onRecorded).toHaveBeenCalledTimes(1)
    const clip = onRecorded.mock.calls[0][0]
    expect(clip.limitReached).toBe(true)
    expect(clip.durationMs).toBe(VOICE_MAX_MS)
    expect(media.track.stop).toHaveBeenCalled()
    expect(result.current.state).toBe('idle')
  })

  it('shows the permission problem instead of swallowing it', async () => {
    installFakeMedia({ getUserMedia: async () => { throw Object.assign(new Error('x'), { name: 'NotAllowedError' }) } })
    const onRecorded = vi.fn()
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toMatch(/permission denied/i)
    expect(FakeRecorder.instances).toHaveLength(0)
    expect(onRecorded).not.toHaveBeenCalled()
  })

  it('names the other microphone problems and the unsupported browser', async () => {
    expect(describeMicError(Object.assign(new Error('x'), { name: 'NotFoundError' }))).toMatch(/no microphone/i)
    expect(describeMicError(Object.assign(new Error('x'), { name: 'NotReadableError' }))).toMatch(/busy or unavailable/i)
    expect(describeMicError(new Error('boom'))).toMatch(/Could not start recording: boom/)
    removeFakeMedia()
    const { result } = renderHook(() => useVoiceRecorder(vi.fn()))
    expect(result.current.supported).toBe(false)
    act(() => result.current.toggle())
    await flush()
    expect(result.current.error).toMatch(/not supported/i)
  })

  it('does not upload an accidental tap', async () => {
    const media = installFakeMedia()
    const onRecorded = vi.fn()
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(100)
    act(() => result.current.toggle())
    await flush()
    expect(onRecorded).not.toHaveBeenCalled()
    expect(result.current.error).toMatch(/too short/i)
    expect(result.current.state).toBe('idle')
    expect(media.track.stop).toHaveBeenCalled()
  })

  it('reports an upload failure and goes back to idle', async () => {
    installFakeMedia()
    const onRecorded = vi.fn().mockRejectedValue(new Error('server said no'))
    const { result } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(1000)
    act(() => result.current.toggle())
    await flush()
    expect(result.current.error).toBe('server said no')
    expect(result.current.state).toBe('idle')
  })

  it('releases the microphone and uploads nothing when the page goes away mid-recording', async () => {
    const media = installFakeMedia()
    const onRecorded = vi.fn()
    const { result, unmount } = renderHook(() => useVoiceRecorder(onRecorded))
    act(() => result.current.toggle())
    await flush()
    await advance(1500)
    unmount()
    await flush()
    expect(media.track.stop).toHaveBeenCalled()
    expect(onRecorded).not.toHaveBeenCalled()
  })
})
