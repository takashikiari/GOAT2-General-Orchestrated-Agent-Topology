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

const HISTORY_WITH_NEW_MESSAGE = {
  ...HISTORY,
  timeline: [...HISTORY.timeline, { tier: 'L2', timestamp: 4, role: 'user', content: 'ce faci?' }],
}

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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText(/Waiting for the current turn to finish/)).toBeInTheDocument())
  })

  it('Stop and Esc cancel the running turn', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }])])
    const post = vi.spyOn(client, 'apiPost').mockImplementation(async (path: string) =>
      (path === '/api/chat' ? { turn_id: 't1' } : { cancelled: true }) as never)
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'lung' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
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
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText(/Session expired/)).toBeInTheDocument())
    expect(screen.getByLabelText('Message GOAT')).toHaveValue('x')
  })

  it('does not duplicate a finished turn once the stored conversation contains it', async () => {
    const get = mockGet([view('done', [{ seq: 1, ts: 1, type: 'reply', text: 'Buna! Totul e ok.' }], 'Buna! Totul e ok.')])
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ce faci?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining('/api/chat/turns/t1')))
    await waitFor(() => expect(screen.queryByTestId('live-turn')).not.toBeInTheDocument(), { timeout: 3000 })
    expect(screen.getAllByText('Buna! Totul e ok.')).toHaveLength(1)
  })

  it('picks up a turn that is already running when the page opens', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }], null, 'mesaj de pe alt device')],
      { ...IDLE, busy: true, active_turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('mesaj de pe alt device')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
  })

  it('shows a stopped tool as stopped, not running', async () => {
    mockGet([view('cancelled', [
      { seq: 1, ts: 1, type: 'started' },
      { seq: 2, ts: 2, type: 'tool_start', id: 'b', name: 'shell_run', args: '{"cmd": "sleep 60"}' },
      { seq: 3, ts: 3, type: 'cancelled' },
    ])])
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ce faci?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByText('Stopped.')).toBeInTheDocument())
    expect(screen.getByTestId('tool-row')).toHaveTextContent('stopped')
    expect(screen.getByTestId('tool-row')).not.toHaveTextContent('running')
  })

  it('shows the user message once when the stored conversation already holds it mid-turn', async () => {
    let posted = false
    vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => {
      if (path.startsWith('/api/chat/state')) return IDLE as never
      if (path.startsWith('/api/conversations/')) return (posted ? HISTORY_WITH_NEW_MESSAGE : HISTORY) as never
      return view('running', [{ seq: 1, ts: 1, type: 'started' }]) as never
    })
    vi.spyOn(client, 'apiPost').mockImplementation(async () => {
      posted = true
      return { turn_id: 't1' } as never
    })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ce faci?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByTestId('live-turn')).toBeInTheDocument())
    await new Promise((r) => setTimeout(r, 2600)) // let the 2 s history poll pick the stored message up
    expect(screen.getAllByText('ce faci?')).toHaveLength(1)
  })

  it('keeps Stop and Send in place all the time: Stop is just disabled while nothing runs', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }])])
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled())
  })

  it('the buttons do not take focus from the message box (the phone keyboard would close and the page jump)', async () => {
    mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }])], { ...IDLE, busy: true, active_turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'x' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled())
    expect(fireEvent.mouseDown(screen.getByRole('button', { name: 'Send' }))).toBe(false)
    expect(fireEvent.mouseDown(screen.getByRole('button', { name: 'Stop' }))).toBe(false)
  })

  describe('scrolling', () => {
    /** jsdom has no layout: give the message list a height, a scroll position and a log of every write to it. */
    function fakeLayout(el: HTMLElement, opts: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
      const state = { ...opts }
      const writes: number[] = []
      Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => state.scrollHeight })
      Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => state.clientHeight })
      Object.defineProperty(el, 'scrollTop', {
        configurable: true,
        get: () => state.scrollTop,
        set: (v: number) => {
          state.scrollTop = v
          writes.push(v)
        },
      })
      return { state, writes }
    }

    async function openAt(opts: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
      render(<Chat />)
      await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
      const scroller = screen.getByTestId('chat-transcript')
      const layout = fakeLayout(scroller, opts)
      return { scroller, ...layout }
    }

    const RUNNING = { ...IDLE, busy: true, active_turn_id: 't1' }
    const GROWS = () => [
      view('running', [{ seq: 1, ts: 1, type: 'started' }]),
      view('running', [{ seq: 2, ts: 2, type: 'tool_start', id: 'a', name: 'get_recent_logs', args: '{}' }]),
    ]

    it('follows new activity while the user is at the bottom', async () => {
      mockGet(GROWS(), RUNNING)
      const { state, writes } = await openAt({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })
      state.scrollHeight = 1300 // the live turn made the list taller
      await waitFor(() => expect(screen.getByTestId('tool-row')).toBeInTheDocument(), { timeout: 3000 })
      expect(writes.length).toBeGreaterThan(0)
      expect(writes[writes.length - 1]).toBe(1300)
    })

    it('does not drag the user back down after they scrolled up, and offers a way back', async () => {
      mockGet(GROWS(), RUNNING)
      const { scroller, state, writes } = await openAt({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })
      state.scrollTop = 100 // the user scrolled up to read
      fireEvent.scroll(scroller)
      const latest = await screen.findByText('↓ Latest')
      const before = writes.length
      state.scrollHeight = 1300
      await waitFor(() => expect(screen.getByTestId('tool-row')).toBeInTheDocument(), { timeout: 3000 })
      expect(writes.length).toBe(before) // nothing moved the list
      expect(state.scrollTop).toBe(100)
      fireEvent.click(latest)
      expect(state.scrollTop).toBe(1300)
      await waitFor(() => expect(screen.queryByText('↓ Latest')).not.toBeInTheDocument())
    })

    it('a poll that brings nothing new does not touch the scroll position', async () => {
      mockGet([view('running', [{ seq: 1, ts: 1, type: 'started' }])], RUNNING)
      const { writes } = await openAt({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })
      await waitFor(() => expect(screen.getByTestId('live-turn')).toBeInTheDocument())
      await new Promise((r) => setTimeout(r, 300))
      const settled = writes.length
      await new Promise((r) => setTimeout(r, 1700)) // two or more further polls of the same turn
      expect(writes.length).toBe(settled)
    })

    it('sending always brings the user back to the bottom', async () => {
      mockGet([view('queued', [])])
      vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
      const { scroller, state } = await openAt({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })
      state.scrollTop = 50
      fireEvent.scroll(scroller)
      await screen.findByText('↓ Latest')
      state.scrollHeight = 1200
      fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'ceva' } })
      fireEvent.click(screen.getByRole('button', { name: 'Send' }))
      await waitFor(() => expect(state.scrollTop).toBe(1200))
    })
  })

  it('the message box grows with the text, up to double its height, and shrinks back when emptied', async () => {
    mockGet([])
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    const box = screen.getByLabelText('Message GOAT') as HTMLTextAreaElement
    let natural = 20
    Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => natural })
    const type = (value: string) => fireEvent.change(box, { target: { value } })
    type('a')
    expect(box.style.height).toBe('58px') // never below its resting two-row height
    natural = 90
    type('a\nb\nc\nd')
    expect(box.style.height).toBe('90px') // follows the text
    expect(box.style.overflowY).toBe('hidden')
    natural = 400
    type('a\n'.repeat(20))
    expect(box.style.height).toBe('116px') // at most double
    expect(box.style.overflowY).toBe('auto') // beyond that it scrolls inside
    natural = 20
    type('')
    expect(box.style.height).toBe('58px')
    expect(box.style.overflowY).toBe('hidden')
  })

  it('the box goes back to its resting height after a message is sent', async () => {
    mockGet([view('queued', [])])
    vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't1' })
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    const box = screen.getByLabelText('Message GOAT') as HTMLTextAreaElement
    let natural = 300
    Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => natural })
    fireEvent.change(box, { target: { value: 'multe\nrânduri\nde\ntext\nlung' } })
    expect(box.style.height).toBe('116px')
    natural = 20
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(box.style.height).toBe('58px'))
    expect(box).toHaveValue('')
  })

  it('stays pinned when the visible list shrinks (the box grew or the keyboard opened), but not for a reader higher up', async () => {
    const observed: Element[] = []
    const callbacks: (() => void)[] = []
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: () => void) {
          callbacks.push(cb)
        }
        observe(el: Element) {
          observed.push(el)
        }
        disconnect() {}
      },
    )
    mockGet([])
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    const scroller = screen.getByTestId('chat-transcript')
    expect(observed).toContain(scroller) // the list itself is watched, not only its content
    let top = 600
    const writes: number[] = []
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 1000 })
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 400 })
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => { top = v; writes.push(v) } })
    callbacks.forEach((cb) => cb()) // the list got smaller while pinned
    expect(writes[writes.length - 1]).toBe(1000)
    top = 100
    fireEvent.scroll(scroller) // the user is reading higher up
    writes.length = 0
    callbacks.forEach((cb) => cb())
    expect(writes).toEqual([])
  })

  it('an empty box keeps its resting height even when the placeholder wraps (it must not grow on its own)', async () => {
    mockGet([])
    render(<Chat />)
    await waitFor(() => expect(screen.getByText('salut din telegram')).toBeInTheDocument())
    const box = screen.getByLabelText('Message GOAT') as HTMLTextAreaElement
    Object.defineProperty(box, 'scrollHeight', { configurable: true, get: () => 78 }) // a three-line placeholder
    fireEvent.change(box, { target: { value: 'x' } })
    fireEvent.change(box, { target: { value: '' } })
    expect(box.style.height).toBe('58px')
  })
})
