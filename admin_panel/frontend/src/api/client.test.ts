import { afterEach, describe, expect, it, vi } from 'vitest'
import * as initDataModule from '../telegram/initData'
import { apiGet } from './client'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('apiGet', () => {
  it('attaches the X-Telegram-Init-Data header', async () => {
    vi.spyOn(initDataModule, 'getInitData').mockReturnValue('signed-data')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    vi.stubGlobal('fetch', fetchMock)

    await apiGet('/api/metrics')

    expect(fetchMock).toHaveBeenCalledWith('/api/metrics', {
      headers: { 'X-Telegram-Init-Data': 'signed-data' },
    })
  })

  it('resolves normally on a 200 with an {error} body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ error: 'Letta unavailable' }) }),
    )
    const result = await apiGet<{ error: string }>('/api/memory/facts')
    expect(result.error).toBe('Letta unavailable')
  })

  it('throws on a non-2xx status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    await expect(apiGet('/api/metrics')).rejects.toThrow('401')
  })
})
