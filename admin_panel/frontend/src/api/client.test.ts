import { afterEach, describe, expect, it, vi } from 'vitest'
import * as initDataModule from '../telegram/initData'
import { ApiError, apiGet, apiPost } from './client'

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

describe('apiPost', () => {
  it('sends JSON with the initData header and returns the body', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ turn_id: 'x' }) })
    vi.stubGlobal('fetch', fetchMock)
    const out = await apiPost<{ turn_id: string }>('/api/chat', { text: 'hi' })
    expect(out).toEqual({ turn_id: 'x' })
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/chat')
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{"text":"hi"}')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.headers).toHaveProperty('X-Telegram-Init-Data')
  })

  it('throws ApiError carrying the status and the FastAPI detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({ detail: 'too many messages waiting' }) }))
    await expect(apiPost('/api/chat', { text: 'hi' })).rejects.toMatchObject({ status: 429, detail: 'too many messages waiting' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => { throw new Error('not json') } }))
    await expect(apiPost('/api/chat')).rejects.toBeInstanceOf(ApiError)
  })
})
