import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Dashboard } from './Dashboard'

afterEach(() => vi.restoreAllMocks())

describe('Dashboard', () => {
  it('renders metrics grouped into cards', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({
      total_requests: 10,
      cache_hit_rate: 0.5,
      cache_miss_rate: 0.5,
      prefetch_attempt_rate: 1,
      prefetch_success_rate: 0.9,
      prefetch_timeout_rate: 0.1,
      tier_hit_rates: { L1: 0.2, L2: 0.5 },
      top_intents: { greeting: 3 },
      avg_tokens_injected: 120,
      avg_latency_total: 1.234,
      avg_llm_calls: 1.5,
      activation_cold_rate: 0.1,
      activation_warm_rate: 0.8,
      activation_drift_rate: 0.1,
      thread_breaks: 2,
      enriching_writes: 5,
      filing_writes: 3,
      warm_served_rate: 0.7,
    })

    render(<Dashboard />)

    await waitFor(() => expect(screen.getByText(/Hit rate: 50.0%/)).toBeInTheDocument())
    expect(screen.getByText(/L1: 20.0%/)).toBeInTheDocument()
    expect(screen.getByText(/greeting: 3/)).toBeInTheDocument()
  })

  it('renders an error banner when the backend call fails', async () => {
    vi.spyOn(client, 'apiGet').mockRejectedValue(new Error('/api/metrics failed: 401'))
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('/api/metrics failed: 401')).toBeInTheDocument())
  })

  describe('tunnel link', () => {
    const METRICS = { cache_hit_rate: 0.5, tier_hit_rates: {}, top_intents: {} }
    const mockApi = (tunnel: unknown) =>
      vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => (path === '/api/tunnel' ? tunnel : METRICS) as never)

    it('shows the current link with its status', async () => {
      mockApi({ status: 'ready', url: 'https://fresh-words.trycloudflare.com', since: 1_700_000_000, detail: '' })
      render(<Dashboard />)
      const link = await screen.findByRole('link', { name: 'https://fresh-words.trycloudflare.com' })
      expect(link).toHaveAttribute('href', 'https://fresh-words.trycloudflare.com')
      expect(screen.getByText('Online')).toBeInTheDocument()
      expect(screen.getByText(/new link is created every time the bot starts/)).toBeInTheDocument()
    })

    it('shows the reason instead of a link when the tunnel is not up', async () => {
      mockApi({ status: 'unavailable', url: null, since: 1_700_000_000, detail: 'cloudflared could not be started (is it in the bot\'s PATH?)' })
      render(<Dashboard />)
      await waitFor(() => expect(screen.getByText('Unavailable')).toBeInTheDocument())
      expect(screen.getByText(/is it in the bot's PATH/)).toBeInTheDocument()
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
    })

    it('still shows the link when the metrics call fails', async () => {
      vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => {
        if (path === '/api/tunnel') return { status: 'ready', url: 'https://a-b.trycloudflare.com', since: 1, detail: '' } as never
        throw new Error('/api/metrics failed: 500')
      })
      render(<Dashboard />)
      expect(await screen.findByRole('link', { name: 'https://a-b.trycloudflare.com' })).toBeInTheDocument()
      expect(screen.getByText('/api/metrics failed: 500')).toBeInTheDocument()
    })

    it('picks up a new link on its own (the bot was restarted)', async () => {
      vi.useFakeTimers()
      try {
        const calls = { n: 0 }
        vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => {
          if (path !== '/api/tunnel') return METRICS as never
          calls.n += 1
          const url = calls.n === 1 ? 'https://old-words.trycloudflare.com' : 'https://new-words.trycloudflare.com'
          return { status: 'ready', url, since: 1, detail: '' } as never
        })
        render(<Dashboard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(10) })
        expect(screen.getByRole('link')).toHaveTextContent('old-words')
        await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
        expect(screen.getByRole('link')).toHaveTextContent('new-words')
      } finally {
        vi.useRealTimers()
      }
    })

    it('copies the link to the clipboard', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
      mockApi({ status: 'ready', url: 'https://fresh-words.trycloudflare.com', since: 1, detail: '' })
      render(<Dashboard />)
      fireEvent.click(await screen.findByRole('button', { name: 'Copy' }))
      await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://fresh-words.trycloudflare.com'))
      expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
    })

    it('shows nothing (and does not break) when the tunnel answer has an unexpected shape', async () => {
      mockApi({ unexpected: true })
      render(<Dashboard />)
      await waitFor(() => expect(screen.getByText(/Hit rate/)).toBeInTheDocument())
      expect(screen.queryByText(/Cloudflare tunnel/)).not.toBeInTheDocument()
    })
  })

  it('shows the live voice minutes of the day against the cap, and says when the cap is reached', async () => {
    const usage = { provider: 'gemini', today_minutes: 12.34, limit_minutes: 60, sessions_today: 3, limit_reached: false }
    const spy = vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      (path === '/api/voice/live/usage' ? usage : {}) as never,
    )
    const { unmount } = render(<Dashboard />)
    await waitFor(() => expect(screen.getByText(/Today: 12.3 \/ 60 min · 3 conversations/)).toBeInTheDocument())
    expect(screen.getByText(/Live voice minutes \(gemini\)/)).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/voice/live/usage')
    unmount()

    Object.assign(usage, { today_minutes: 60, sessions_today: 1, limit_reached: true })
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText(/Daily limit reached/)).toBeInTheDocument())
    expect(screen.getByText(/1 conversation$/)).toBeInTheDocument()
  })

  it('has no live voice card when the server has no live voice', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      (path === '/api/voice/live/usage' ? { provider: null, today_minutes: 0, limit_minutes: 60, sessions_today: 0, limit_reached: false } : {}) as never,
    )
    render(<Dashboard />)
    await act(async () => {})
    expect(screen.queryByText(/Live voice minutes/)).toBeNull()
  })
})
