import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Dashboard } from './Dashboard'

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
})
