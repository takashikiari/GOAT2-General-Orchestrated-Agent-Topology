import { render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { Agents } from './Agents'

afterEach(() => vi.restoreAllMocks())

const TURNS = [
  {
    timestamp: 1_700_000_000, reply_timestamp: 1_700_000_030,
    messages: [{ from: 'lenovo/agent-coord/ee1c5e', to: 'lenovo/goat', text: 'PR #36 mergeuit', alert: false, origin_warning: null }],
    goat_reply: 'Am citit: #36 e mergeuit.', tools: 'called repo_git({})',
  },
  {
    timestamp: 1_700_000_500, reply_timestamp: null,
    messages: [
      { from: 'lenovo/goat/watchdog', to: 'lenovo/goat/superv', text: 'inactivă 30 min', alert: true, origin_warning: null },
      { from: 'laris/home/x', to: 'lenovo/goat', text: 'text', alert: false, origin_warning: 'alt-host' },
    ],
    goat_reply: null, tools: null,
  },
]

describe('Agents', () => {
  it('lists the agent-coord messages newest first, with their senders, flags and GOAT reply', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ chat_id: '42', turns: TURNS })
    render(<Agents />)
    const cards = await screen.findAllByTestId('agent-turn')
    expect(cards).toHaveLength(2)
    expect(within(cards[0]).getByText('lenovo/goat/watchdog')).toBeInTheDocument() // newest first
    expect(within(cards[0]).getByText('supervisor alert')).toBeInTheDocument()
    expect(within(cards[0]).getByText('real origin: alt-host')).toBeInTheDocument()
    expect(within(cards[1]).getByText('lenovo/agent-coord/ee1c5e')).toBeInTheDocument()
    expect(within(cards[1]).getByText('PR #36 mergeuit')).toBeInTheDocument()
    expect(within(cards[1]).getByText('Am citit: #36 e mergeuit.')).toBeInTheDocument()
    expect(within(cards[1]).getByText('called repo_git({})')).toBeInTheDocument()
    expect(within(cards[0]).queryByText(/GOAT's reply/)).not.toBeInTheDocument() // no reply for that batch
    expect(client.apiGet).toHaveBeenCalledWith('/api/agents')
  })

  it('folds a long message to its first lines and opens the full text in place', async () => {
    const long = 'x'.repeat(900)
    vi.spyOn(client, 'apiGet').mockResolvedValue({ chat_id: '42', turns: [{ ...TURNS[0], messages: [{ ...TURNS[0].messages[0], text: long }] }] })
    render(<Agents />)
    await screen.findByTestId('agent-turn')
    const summary = screen.getByText(`${'x'.repeat(320)}…`)
    expect(summary.tagName).toBe('SUMMARY')
    expect(screen.getAllByText(long, { exact: true })).toHaveLength(1)
  })

  it('says so when there is nothing yet', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ chat_id: '42', turns: [] })
    render(<Agents />)
    await waitFor(() => expect(screen.getByText(/No agent-coord messages/)).toBeInTheDocument())
  })

  it('shows a failure and a storage warning without hiding what loaded', async () => {
    vi.spyOn(client, 'apiGet').mockResolvedValue({ chat_id: '42', turns: TURNS, warnings: ['Redis unavailable'] })
    render(<Agents />)
    expect(await screen.findByText('Redis unavailable')).toBeInTheDocument()
    expect(screen.getAllByTestId('agent-turn')).toHaveLength(2)
  })

  it('shows an error banner when the call fails', async () => {
    vi.spyOn(client, 'apiGet').mockRejectedValue(new Error('/api/agents failed: 401'))
    render(<Agents />)
    expect(await screen.findByText('/api/agents failed: 401')).toBeInTheDocument()
  })
})
