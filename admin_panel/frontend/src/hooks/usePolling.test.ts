import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling } from './usePolling'

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('fetches immediately on mount', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    const { result } = renderHook(() => usePolling(fetcher, 15000, []))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.data).toEqual({ ok: true })
  })

  it('refetches on each interval tick', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    renderHook(() => usePolling(fetcher, 15000, []))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })

    expect(fetcher).toHaveBeenCalledTimes(3)
  })

  it('clears the interval on unmount', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    const { unmount } = renderHook(() => usePolling(fetcher, 15000, []))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000)
    })

    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('restarts polling when deps change', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    const { rerender } = renderHook(({ chatId }) => usePolling(fetcher, 15000, [chatId]), {
      initialProps: { chatId: 'a' },
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetcher).toHaveBeenCalledTimes(1)

    rerender({ chatId: 'b' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('surfaces a rejected fetch as error, not a thrown exception', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('network down'))
    const { result } = renderHook(() => usePolling(fetcher, 15000, []))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(result.current.error).toBe('network down')
  })

  it('clears data to null immediately when deps change, before the new fetch resolves', async () => {
    // Each call returns a promise this test controls the resolution of, so
    // we can inspect `data` while the new fetch is still in flight.
    let resolveSecond: (value: { chatId: string }) => void = () => {}
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ chatId: 'a' })
      .mockImplementationOnce(
        () =>
          new Promise<{ chatId: string }>((resolve) => {
            resolveSecond = resolve
          }),
      )

    const { result, rerender } = renderHook(({ chatId }) => usePolling(fetcher, 15000, [chatId]), {
      initialProps: { chatId: 'a' },
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toEqual({ chatId: 'a' })

    rerender({ chatId: 'b' })

    // The new fetch for 'b' hasn't resolved yet — data must not still show 'a'.
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)

    await act(async () => {
      resolveSecond({ chatId: 'b' })
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toEqual({ chatId: 'b' })
  })

  it('does not clear data between interval ticks with unchanged deps (no flicker)', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    const { result } = renderHook(() => usePolling(fetcher, 15000, ['same']))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toEqual({ ok: true })

    // Advance to just before the interval fires again — data must still be
    // the previous value, never null, at any point around a tick.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14999)
    })
    expect(result.current.data).toEqual({ ok: true })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(result.current.data).toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
