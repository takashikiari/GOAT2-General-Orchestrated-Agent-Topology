import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

describe('App', () => {
  it('shows the Dashboard tab active by default', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('panel-dashboard')).toBeInTheDocument()
  })

  it('switches the active tab on click', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Logs' }))
    expect(screen.getByRole('button', { name: 'Logs' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('panel-logs')).toBeInTheDocument()
  })

  it('wires App -> Dashboard -> usePolling -> apiGet -> fetch together end to end', async () => {
    // Unlike every other test in this suite, this does NOT mock client.apiGet.
    // It stubs `fetch` directly so the real apiGet/getInitData code paths run,
    // proving the whole chain is actually wired up correctly.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
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
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await waitFor(() => expect(screen.getByText(/Hit rate: 50.0%/)).toBeInTheDocument())

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/metrics',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Telegram-Init-Data': expect.any(String) }),
      }),
    )
  })
})
