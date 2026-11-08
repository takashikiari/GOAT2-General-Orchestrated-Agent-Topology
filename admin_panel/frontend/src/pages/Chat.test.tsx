import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Chat } from './Chat'

const IDLE = { chat_id: '42', busy: false, external_busy: false, active_turn_id: null }
const HISTORY = {
  chat_id: '42',
  timeline: [
    { tier: 'L3', timestamp: 1, role: '', content: 'archived memory entry' },
    { tier: 'L2', timestamp: 2, role: 'user', content: 'salut din telegram' },
    { tier: 'L2', timestamp: 3, role: 'assistant', content: '[Tool calls]\ncalled get_recent_logs({})\n\nBuna! Totul e ok.' },
  ],
}

/** apiGet that serves state + history and a scripted sequence of turn views. */
function mockGet(turnViews: unknown[], state: unknown = IDLE) {
  let n = 0
  return vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => {
    if (path.startsWith('/api/chat/state')) return state as never
    if (path.startsWith('/api/conversations/')) return HISTORY as never
    if (path.startsWith('/api/chat/turns/')) return turnViews[Math.min(n++, turnViews.length - 1)] as never
    throw new Error(`unexpected GET ${path}`)
  })
}

const view = (status: string, events: unknown[], reply: string | null = null, text = 'ce faci?') => ({
  id: 't1', text, status, reply, events,
})

afterEach(() => vi.restoreAllMocks())

describe('Chat', () => {
  it('shows the Telegram conversation (user and assistant messages only) and folds the tool summary', async () => {
    mockGet([])
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    expect(screen.getByText('Buna! Totul e ok.')).toBeInTheDocument()
    expect(screen.getByText('called get_recent_logs({})')).toBeInTheDocument()
    expect(screen.queryByText('archived memory entry')).not.toBeInTheDocument()
    expect(screen.getByText(/one conversation with Telegram/)).toBeInTheDocument()
  })

  it('sends a message, shows the tool call live, then the reply', async () => {
    mockGet([
      view('running', [
        { seq: 1, ts: 1, type: 'started' },
        { seq: 2, ts: 2, type: 'tool_start', id: 'a', name: 'get_recent_logs', args: '{"minutes": 5}' },
      ]),
      view('done', [
        { seq: 3, ts: 3, type: 'tool_end', id: 'a', name: 'get_recent_logs', chars: 1234, ok: true, preview: 'linii' },
        { seq: 4, ts: 4, type: 'reply', text: 'Gata, totul merge.' },
      ], 'Gata, totul merge.'),
    ])
    const post = vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ce faci?' } })
    fireEvent.click(screen.getByText('Send'))

    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/chat', { text: 'ce faci?' }))
    await waitFor(() => expect(screen.getByTestId('tool-row')).toHaveTextContent('get_recent_logs'))
    expect(screen.getByTestId('tool-row')).toHaveTextContent('running')
    expect(screen.getByText('ce faci?')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Gata, totul merge.')).toBeInTheDocument(), { timeout: 3000 })
    expect(screen.getByTestId('tool-row')).toHaveTextContent('1234 chars')
    expect(screen.getByLabelText('Message GOAT')).toHaveValue('')
  })

  it('Enter sends, Shift+Enter does not', async () => {
    mockGet([view('queued', [])])
    const post = vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    const box = screen.getByLabelText('Message GOAT')
    fireEvent.change(box, { target: { value: 'a' } })
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(post).not.toHaveBeenCalled()
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/chat', { text: 'a' }))
  })

  it('shows that the message is waiting behind a Telegram turn', async () => {
    mockGet([view('queued', [{ seq: 1, ts: 1, type: 'queued' }])], { ...IDLE, busy: true, external_busy: true })
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText(/answering on Telegram/)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'x' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText(/Waiting for the current turn to finish/)).toBeInTheDocument())
  })

  it('Stop and Esc cancel the running turn', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }])])
    const post = vi.spyOn(client, 'apiPost').mockImplementation(async (path: string) =>
      (path === '/api/chat' ? { turn_id: 't1' } : { cancelled: true }) as never)
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'lung' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Stop')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Stop'))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/chat/turns/t1/cancel'))
    post.mockClear()
    fireEvent.keyDown(screen.getByLabelText('Message GOAT'), { key: 'Escape' })
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/chat/turns/t1/cancel'))
  })

  it('explains an expired session instead of a bare status code', async () => {
    mockGet([])
    vi.spyOn(client, 'apiPost').mockRejectedValue(new client.ApiError('/api/chat failed: 401', 401, 'invalid or expired initData'))
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'x' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText(/Session expired/)).toBeInTheDocument())
    expect(screen.getByLabelText('Message GOAT')).toHaveValue('x')
  })

  it('does not duplicate a finished turn once the stored conversation contains it', async () => {
    const get = mockGet([view('done', [{ seq: 1, ts: 1, type: 'reply', text: 'Buna! Totul e ok.' }], 'Buna! Totul e ok.')])
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ce faci?' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining('/api/chat/turns/t1')))
    await waitFor(() => expect(screen.queryByTestId('live-turn')).not.toBeInTheDocument(), { timeout: 3000 })
    expect(screen.getAllByText('Buna! Totul e ok.')).toHaveLength(1)
  })

  it('picks up a turn that is already running when the page opens', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }], null, 'mesaj de pe alt device')],
      { ...IDLE, busy: true, active_turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('mesaj de pe alt device')).toBeInTheDocument())
    expect(screen.getByText('Stop')).toBeInTheDocument()
  })
})
