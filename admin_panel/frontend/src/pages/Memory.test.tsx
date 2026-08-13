import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Memory } from './Memory'

describe('Memory', () => {
  it('renders L1 facts by default', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockResolvedValue({ facts: { name: 'Gabriel' } })
    render(<Memory />)
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/memory/facts'))
    expect(screen.getByText(/Gabriel/)).toBeInTheDocument()
  })

  it('does not fetch working memory until a chat_id is entered', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockResolvedValue({ facts: {} })
    render(<Memory />)
    fireEvent.click(screen.getByText('working'))
    await waitFor(() => expect(screen.getByText('Enter a chat_id above.')).toBeInTheDocument())
    expect(spy).not.toHaveBeenCalledWith(expect.stringContaining('/api/memory/working/'))
  })

  it('fetches working memory once a chat_id is entered', async () => {
    const spy = vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/memory/working/')
        ? { messages: [{ role: 'user', content: 'hi' }] }
        : { facts: {} },
    )
    render(<Memory />)
    fireEvent.click(screen.getByText('working'))
    fireEvent.change(screen.getByLabelText(/chat_id/i), { target: { value: 'chat1' } })
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/memory/working/chat1'))
  })

  it('renders the backend error banner for episodic', async () => {
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) =>
      path.startsWith('/api/memory/episodic/') ? { error: 'ChromaDB unavailable: x' } : { facts: {} },
    )
    render(<Memory />)
    fireEvent.click(screen.getByText('episodic'))
    fireEvent.change(screen.getByLabelText(/chat_id/i), { target: { value: 'chat1' } })
    await waitFor(() => expect(screen.getByText('ChromaDB unavailable: x')).toBeInTheDocument())
  })
})
