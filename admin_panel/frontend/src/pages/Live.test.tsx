import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Live, parseLogLine } from './Live'

describe('parseLogLine', () => {
  it('parses timestamp, level, and message from a real log line', () => {
    const parsed = parseLogLine('2026-08-14T07:23:41  telegram_interface.bot  INFO      Starting GOAT 2.0 Telegram bot')
    expect(parsed.level).toBe('INFO')
    expect(parsed.message).toBe('Starting GOAT 2.0 Telegram bot')
    expect(parsed.timestamp).not.toBeNull()
  })

  it('falls back to raw text with no level when the line does not match the format', () => {
    const parsed = parseLogLine('not a structured log line')
    expect(parsed.level).toBe('')
    expect(parsed.message).toBe('not a structured log line')
    expect(parsed.timestamp).toBeNull()
  })
})

describe('Live', () => {
  it('shows an empty state before any logs arrive', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs') ? { lines: [] } : { total_requests: 0, tier_hit_rates: {} },
    )
    render(<Live />)
    await waitFor(() => expect(screen.getByText(/Waiting for activity/)).toBeInTheDocument())
  })

  it('renders parsed log lines with their level', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs')
        ? { lines: ['2026-08-14T07:23:41  mod  ERROR     something broke'] }
        : { total_requests: 5, avg_latency_total: 1.2, tier_hit_rates: { L1: 0.5 } },
    )
    render(<Live />)
    await waitFor(() => expect(screen.getByText('something broke')).toBeInTheDocument())
    expect(screen.getByText('ERROR')).toBeInTheDocument()
  })

  it('refetches with the new level when a quick filter is clicked', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs') ? { lines: [] } : { total_requests: 0, tier_hit_rates: {} },
    )
    render(<Live />)
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringContaining('level=ALL')))

    fireEvent.click(screen.getByRole('button', { name: 'Error' }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.stringContaining('level=ERROR')))
  })

  it('renders the desktop metrics panel with processed count, latency, and tier hit rates', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs')
        ? { lines: [] }
        : { total_requests: 42, avg_latency_total: 0.987, tier_hit_rates: { working: 0.25, episodic: 0.5 } },
    )
    render(<Live />)
    await waitFor(() => expect(screen.getByText('42')).toBeInTheDocument())
    expect(screen.getByText('0.99s')).toBeInTheDocument()
    expect(screen.getByText('working')).toBeInTheDocument()
  })

  it('always shows all three known memory tiers, even ones absent from the response', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs') ? { lines: [] } : { total_requests: 0, tier_hit_rates: { episodic: 1 } },
    )
    render(<Live />)
    await waitFor(() => expect(screen.getByText('episodic')).toBeInTheDocument())
    expect(screen.getByText('working')).toBeInTheDocument()
    expect(screen.getByText('permanent')).toBeInTheDocument()
  })

  it('pauses auto-scroll when the user scrolls away from the bottom, and resumes on click', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/logs') ? { lines: ['2026-08-14T07:23:41  mod  INFO      hi'] } : { total_requests: 0, tier_hit_rates: {} },
    )
    render(<Live />)
    await waitFor(() => expect(screen.getByText('hi')).toBeInTheDocument())

    const scrollContainer = screen.getByText('hi').closest('[class*="overflow-y-auto"]') as HTMLElement
    Object.defineProperty(scrollContainer, 'scrollHeight', { value: 1000, configurable: true })
    Object.defineProperty(scrollContainer, 'clientHeight', { value: 200, configurable: true })
    Object.defineProperty(scrollContainer, 'scrollTop', { value: 100, configurable: true, writable: true })

    fireEvent.scroll(scrollContainer)

    const resumeButton = await screen.findByRole('button', { name: /Resume live scroll/i })
    expect(resumeButton).toBeInTheDocument()

    fireEvent.click(resumeButton)
    expect(screen.queryByRole('button', { name: /Resume live scroll/i })).not.toBeInTheDocument()
  })
})
