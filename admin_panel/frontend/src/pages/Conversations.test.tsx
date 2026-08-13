import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Conversations } from './Conversations'

describe('Conversations', () => {
  it('lists chat_ids and loads a timeline on click', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => {
      if (path === '/api/conversations') {
        return { conversations: [{ chat_id: 'chat1', status: 'active' }] }
      }
      return { chat_id: 'chat1', timeline: [{ tier: 'L2', timestamp: 1, role: 'user', content: 'hello' }] }
    })

    render(<Conversations />)
    await waitFor(() => expect(screen.getByText(/chat1/)).toBeInTheDocument())

    fireEvent.click(screen.getByText(/chat1/))

    await waitFor(() => expect(screen.getByText(/hello/)).toBeInTheDocument())
  })

  it('renders warnings without hiding partial data', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({
      conversations: [{ chat_id: 'chat1', status: 'active' }],
      warnings: ['Redis unavailable: x'],
    })
    render(<Conversations />)
    await waitFor(() => expect(screen.getByText('Redis unavailable: x')).toBeInTheDocument())
    expect(screen.getByText(/chat1/)).toBeInTheDocument()
  })
})
