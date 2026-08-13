import { afterEach, describe, expect, it, vi } from 'vitest'
import { getInitData } from './initData'

afterEach(() => {
  delete (window as { Telegram?: unknown }).Telegram
  vi.unstubAllEnvs()
})

describe('getInitData', () => {
  it('returns window.Telegram.WebApp.initData when present', () => {
    ;(window as { Telegram?: unknown }).Telegram = {
      WebApp: { initData: 'real-data', ready: () => {} },
    }
    expect(getInitData()).toBe('real-data')
  })

  it('falls back to VITE_DEV_INIT_DATA in dev mode when Telegram is absent', () => {
    vi.stubEnv('DEV', true)
    vi.stubEnv('VITE_DEV_INIT_DATA', 'fake-dev-data')
    expect(getInitData()).toBe('fake-dev-data')
  })

  it('returns empty string when Telegram is absent and not in dev mode', () => {
    vi.stubEnv('DEV', false)
    expect(getInitData()).toBe('')
  })
})
