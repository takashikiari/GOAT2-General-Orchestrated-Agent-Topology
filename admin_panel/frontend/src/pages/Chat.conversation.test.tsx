import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'

const conv = vi.hoisted(() => {
  const fns = { start: vi.fn(), end: vi.fn(), toggleMute: vi.fn(), interrupt: vi.fn(), setVoiceKind: vi.fn() }
  const base = { active: false, phase: 'listening', muted: false, interim: '', error: null as string | null, notice: null as string | null, elapsedMs: 0, voiceKind: 'deepgram' }
  return { args: null as null | { submit: (t: string) => Promise<string | null>; turns: unknown[] }, fns, value: { ...base, ...fns }, base }
})
vi.mock('../voice/useConversation', () => ({
  useConversation: (args: { submit: (t: string) => Promise<string | null>; turns: unknown[] }) => {
    conv.args = args
    return conv.value
  },
}))

import { Chat } from './Chat'

const IDLE = { chat_id: '42', busy: false, external_busy: false, active_turn_id: null }
const HISTORY = { chat_id: '42', messages: [{ timestamp: 1, role: 'user', content: 'salut' }] }
const set = (patch: Partial<typeof conv.base>) => { conv.value = { ...conv.base, ...conv.fns, ...patch } }

async function open() {
  vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => (path.startsWith('/api/chat/state') ? IDLE : HISTORY) as never)
  render(<Chat />)
  await waitFor(() => expect(screen.getByText('salut')).toBeInTheDocument())
}

beforeEach(() => {
  Object.values(conv.fns).forEach((f) => f.mockClear())
  set({})
})
afterEach(() => vi.restoreAllMocks())

describe('Chat voice conversation', () => {
  it('has a small Conversation button in the status row that starts it', async () => {
    await open()
    const toggle = screen.getByRole('button', { name: 'Start voice conversation' })
    expect(toggle).toHaveTextContent('Conversation')
    fireEvent.click(toggle)
    expect(conv.fns.start).toHaveBeenCalledTimes(1)
  })

  it('shows which voice reads the replies and switches it with one tap', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Voice: Deepgram' }))
    expect(conv.fns.setVoiceKind).toHaveBeenCalledWith('browser')
  })

  it('shows the browser voice when that one is chosen', async () => {
    set({ voiceKind: 'browser' })
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Voice: browser' }))
    expect(conv.fns.setVoiceKind).toHaveBeenCalledWith('deepgram')
  })

  it('while listening: live words over the box, Mute in the mic slot, End in the send slot, nothing else', async () => {
    set({ active: true, phase: 'listening', interim: 'verifică logurile', elapsedMs: 7000 })
    await open()
    expect(screen.getByTestId('conversation-overlay')).toHaveTextContent('verifică logurile')
    expect(screen.getByRole('button', { name: 'End voice conversation' })).toHaveTextContent('On · 0:07')
    expect(screen.getByRole('button', { name: 'Mute microphone' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'End conversation' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record voice message' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Message GOAT')).toHaveAttribute('readonly')
  })

  it.each([
    ['connecting', 'Connecting…'],
    ['thinking', 'GOAT is thinking…'],
    ['listening', 'Listening…'],
  ] as const)('shows the %s phase over the box', async (phase, text) => {
    set({ active: true, phase })
    await open()
    expect(screen.getByTestId('conversation-overlay')).toHaveTextContent(text)
  })

  it('while GOAT speaks, the overlay is a button that interrupts it', async () => {
    set({ active: true, phase: 'speaking' })
    await open()
    const overlay = screen.getByRole('button', { name: 'Interrupt GOAT' })
    expect(overlay).toHaveTextContent('Speaking — tap to interrupt')
    fireEvent.click(overlay)
    expect(conv.fns.interrupt).toHaveBeenCalledTimes(1)
  })

  it('mute toggles and shows "Muted"', async () => {
    set({ active: true, phase: 'listening', muted: true })
    await open()
    expect(screen.getByTestId('conversation-overlay')).toHaveTextContent('Muted')
    fireEvent.click(screen.getByRole('button', { name: 'Unmute microphone' }))
    expect(conv.fns.toggleMute).toHaveBeenCalledTimes(1)
  })

  it('End, the status toggle and Esc all end the conversation', async () => {
    set({ active: true, phase: 'listening' })
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'End conversation' }))
    fireEvent.click(screen.getByRole('button', { name: 'End voice conversation' }))
    fireEvent.keyDown(screen.getByLabelText('Message GOAT'), { key: 'Escape' })
    expect(conv.fns.end).toHaveBeenCalledTimes(3)
  })

  it('what is said is sent to GOAT like a typed message and the turn id goes back to the conversation', async () => {
    await open()
    const post = vi.spyOn(client, 'apiPost').mockResolvedValue({ turn_id: 't9' })
    let id: string | null = null
    await waitFor(async () => { id = await conv.args!.submit('verifică logurile') })
    expect(post).toHaveBeenCalledWith('/api/chat', { text: 'verifică logurile' })
    expect(id).toBe('t9')
    await waitFor(() => expect(screen.getByText('verifică logurile')).toBeInTheDocument()) // shown as GOAT's turn in the chat
  })

  it('a message that could not be sent gives null and an error under the box', async () => {
    await open()
    vi.spyOn(client, 'apiPost').mockRejectedValue(new client.ApiError('/api/chat failed: 429', 429, 'too many messages waiting'))
    let id: string | null = 'x'
    await waitFor(async () => { id = await conv.args!.submit('salut') })
    expect(id).toBeNull()
    await waitFor(() => expect(screen.getByText(/Too many messages are waiting/)).toBeInTheDocument())
  })

  it('shows the conversation error and notice', async () => {
    set({ error: 'Microphone permission denied.' })
    await open()
    expect(screen.getByText('Microphone permission denied.')).toBeInTheDocument()
  })

  it('shows the conversation notice when there is no error', async () => {
    set({ notice: 'Conversation ended after 90 seconds of silence.' })
    await open()
    expect(screen.getByText('Conversation ended after 90 seconds of silence.')).toBeInTheDocument()
  })

  it('the Conversation button is off while a voice message is being recorded', async () => {
    await open()
    expect(screen.getByRole('button', { name: 'Start voice conversation' })).toBeEnabled()
  })
})
