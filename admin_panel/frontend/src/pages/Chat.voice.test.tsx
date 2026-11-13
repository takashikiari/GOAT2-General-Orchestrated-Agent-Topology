import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../api/client'
import { FakeRecorder, installFakeMedia } from '../test/fakeMedia'
import { Chat } from './Chat'

const IDLE = { chat_id: '42', busy: false, external_busy: false, active_turn_id: null }
const HISTORY = { chat_id: '42', messages: [{ timestamp: 1, role: 'user', content: 'salut' }] }

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const mic = (name: RegExp) => screen.getByRole('button', { name })

async function open() {
  vi.spyOn(client, 'apiGet').mockImplementation(async (path: string) => (path.startsWith('/api/chat/state') ? IDLE : HISTORY) as never)
  render(<Chat />)
  await flush()
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('Chat voice button', () => {
  it('starts idle with a microphone button and no recording overlay', async () => {
    installFakeMedia()
    await open()
    expect(mic(/Record voice message/)).toBeEnabled()
    expect(screen.queryByTestId('voice-overlay')).not.toBeInTheDocument()
  })

  it('idle → recording (timer, stop button) → sending → idle, and says so when nothing was understood', async () => {
    installFakeMedia()
    let answer: (v: unknown) => void = () => {}
    const post = vi.spyOn(client, 'apiPostForm').mockImplementation(() => new Promise((resolve) => { answer = resolve }) as never)
    await open()

    fireEvent.click(mic(/Record voice message/))
    await flush()
    expect(screen.getByTestId('voice-overlay')).toHaveTextContent('Recording 0:00 / 1:00')
    expect(mic(/Stop recording/)).toBeEnabled()
    expect(screen.getByLabelText('Message GOAT')).toHaveAttribute('readonly')
    await advance(7000)
    expect(screen.getByTestId('voice-overlay')).toHaveTextContent('Recording 0:07 / 1:00')

    fireEvent.click(mic(/Stop recording/))
    await flush()
    expect(screen.getByTestId('voice-overlay')).toHaveTextContent('Sending audio')
    expect(mic(/Record voice message/)).toBeDisabled()
    expect(post).toHaveBeenCalledTimes(1)
    const [path, form] = post.mock.calls[0] as [string, FormData]
    expect(path).toBe('/api/voice/message')
    const file = form.get('audio') as File
    expect(file.name).toBe('voice.webm')
    expect(Number(form.get('duration_ms'))).toBeGreaterThanOrEqual(7000)
    const saidAt = Number(form.get('said_at'))
    expect(saidAt).toBeGreaterThan(0)
    expect(saidAt).toBeLessThanOrEqual(Date.now() / 1000 - 7) // when he started speaking, not when he stopped

    await act(async () => { answer({ turn_id: null, transcript: '' }) })
    await flush()
    expect(screen.queryByTestId('voice-overlay')).not.toBeInTheDocument()
    expect(mic(/Record voice message/)).toBeEnabled()
    expect(screen.getByText('N-am înțeles mesajul vocal. Poți să-l repeți?')).toBeInTheDocument()
    expect(screen.getByLabelText('Message GOAT')).not.toHaveAttribute('readonly')
  })

  it('sends a voice message straight to GOAT as a turn of the conversation (not into the text box)', async () => {
    installFakeMedia()
    vi.spyOn(client, 'apiPostForm').mockResolvedValue({ turn_id: 't1', transcript: 'verifică logurile' })
    await open()
    fireEvent.change(screen.getByLabelText('Message GOAT'), { target: { value: 'te rog' } })
    fireEvent.click(mic(/Record voice message/))
    await flush()
    await advance(2000)
    fireEvent.click(mic(/Stop recording/))
    await flush()
    expect(screen.getByText('verifică logurile')).toBeInTheDocument()       // shown as Laris's message in the chat
    expect(screen.getByLabelText('Message GOAT')).toHaveValue('te rog')     // the typed draft is left alone
  })

  it('shows a refused microphone clearly', async () => {
    installFakeMedia({ getUserMedia: async () => { throw Object.assign(new Error('x'), { name: 'NotAllowedError' }) } })
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    expect(screen.getByText(/Microphone permission denied/)).toBeInTheDocument()
    expect(screen.queryByTestId('voice-overlay')).not.toBeInTheDocument()
    expect(mic(/Record voice message/)).toBeEnabled()
  })

  it('says so when this browser cannot record', async () => {
    installFakeMedia()
    vi.stubGlobal('MediaRecorder', undefined)
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    expect(screen.getByText(/Voice recording is not supported here/)).toBeInTheDocument()
  })

  it('sends mp4 as .m4a where webm cannot be recorded (iOS)', async () => {
    installFakeMedia()
    FakeRecorder.supported = new Set(['audio/mp4'])
    const post = vi.spyOn(client, 'apiPostForm').mockResolvedValue({ turn_id: null, transcript: '' })
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    await advance(2000)
    fireEvent.click(mic(/Stop recording/))
    await flush()
    expect(((post.mock.calls[0] as [string, FormData])[1].get('audio') as File).name).toBe('voice.m4a')
  })

  it('stops by itself at 60 s and mentions the limit', async () => {
    installFakeMedia()
    const post = vi.spyOn(client, 'apiPostForm').mockResolvedValue({ turn_id: 't1', transcript: 'un mesaj lung' })
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    await advance(60_200)
    expect(post).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Stopped at the 60 s limit/)).toBeInTheDocument()
  })

  it('explains an upload the server refused', async () => {
    installFakeMedia()
    vi.spyOn(client, 'apiPostForm').mockRejectedValue(new client.ApiError('/api/voice failed: 413', 413, 'audio larger than 5242880 bytes'))
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    await advance(2000)
    fireEvent.click(mic(/Stop recording/))
    await flush()
    expect(screen.getByText('That recording is too large to upload.')).toBeInTheDocument()
    expect(mic(/Record voice message/)).toBeEnabled()
  })

  it('explains an expired session', async () => {
    installFakeMedia()
    vi.spyOn(client, 'apiPostForm').mockRejectedValue(new client.ApiError('/api/voice failed: 401', 401, 'invalid or expired initData'))
    await open()
    fireEvent.click(mic(/Record voice message/))
    await flush()
    await advance(2000)
    fireEvent.click(mic(/Stop recording/))
    await flush()
    expect(screen.getByText(/Session expired/)).toBeInTheDocument()
  })
})
