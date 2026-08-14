import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Logs } from './Logs'

describe('Logs', () => {
  it('renders log lines from the default query', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockResolvedValue({ lines: ['line one', 'line two'] })
    render(<Logs />)
    await waitFor(() => expect(screen.getByText(/line one/)).toBeInTheDocument())
    expect(spy).toHaveBeenCalledWith('/api/logs?minutes=30&level=ALL&limit=100')
  })

  it('refetches with the new level when the filter changes', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockResolvedValue({ lines: [] })
    render(<Logs />)
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/logs?minutes=30&level=ALL&limit=100'))

    fireEvent.change(screen.getByLabelText(/Level/i), { target: { value: 'ERROR' } })

    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/logs?minutes=30&level=ERROR&limit=100'))
  })

  it('renders the backend error banner', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ error: 'log file not found: x.log' })
    render(<Logs />)
    await waitFor(() => expect(screen.getByText('log file not found: x.log')).toBeInTheDocument())
  })
})
