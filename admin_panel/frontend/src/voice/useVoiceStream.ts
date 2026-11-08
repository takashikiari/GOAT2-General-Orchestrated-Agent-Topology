import { useCallback, useEffect, useRef, useState } from 'react'
import { describeMicError } from '../hooks/useVoiceRecorder'
import { getInitData } from '../telegram/initData'
import { startPcmCapture } from './pcmCapture'
import type { PcmCapture } from './pcmCapture'

export type StreamState = 'idle' | 'connecting' | 'listening'
type Handlers = {
  /** One finished utterance (a pause in speech), as text. Not called while paused. */
  onUtterance: (text: string) => void
  /** Something was heard (speech started or words came in): used to reset the silence timer. */
  onActivity?: () => void
}

/** What the server's error codes mean to the person using the page. */
const SERVER_ERRORS: Record<string, string> = {
  unauthorized: 'Session expired — reopen the panel from the Telegram menu button.',
  not_configured: 'Voice conversation is not set up on the server yet (the Deepgram key is missing).',
  upstream_auth: 'The speech recognition service rejected the server key.',
  upstream: 'The speech recognition service is not reachable right now.',
  time_limit: 'The conversation reached its 30 minute limit.',
  frame_too_large: 'The audio could not be sent (frame too large).',
}

function socketUrl(): string {
  const url = new URL('/api/voice/stream', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/**
 * The microphone streamed to the server's live speech-to-text (Deepgram, via our WebSocket proxy) and turned into
 * utterances. Pausing stops the audio (and drops anything half-heard): the conversation does this while GOAT thinks
 * and speaks so it never listens to itself.
 */
export function useVoiceStream(handlers: Handlers) {
  const [state, setState] = useState<StreamState>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const socket = useRef<WebSocket | null>(null)
  const media = useRef<MediaStream | null>(null)
  const capture = useRef<PcmCapture | null>(null)
  const finals = useRef<string[]>([])
  const paused = useRef(false)
  const closing = useRef(false)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const cleanup = useCallback(() => {
    capture.current?.stop()
    capture.current = null
    media.current?.getTracks().forEach((t) => t.stop())
    media.current = null
    const sock = socket.current
    socket.current = null
    if (sock && sock.readyState <= 1) {
      try {
        if (sock.readyState === 1) sock.send(JSON.stringify({ type: 'close' }))
      } catch {
        /* already closing */
      }
      sock.close()
    }
    finals.current = []
    setInterim('')
  }, [])

  const stop = useCallback(() => {
    closing.current = true
    cleanup()
    setState('idle')
  }, [cleanup])

  const fail = useCallback(
    (message: string) => {
      closing.current = true
      cleanup()
      setError(message)
      setState('idle')
    },
    [cleanup],
  )

  const flush = useCallback(() => {
    const text = finals.current.join(' ').trim()
    finals.current = []
    setInterim('')
    if (text && !paused.current) handlersRef.current.onUtterance(text)
  }, [])

  const setPaused = useCallback((p: boolean) => {
    paused.current = p
    capture.current?.setPaused(p)
    if (p) {
      finals.current = []
      setInterim('')
    }
  }, [])

  const start = useCallback(async () => {
    setError(null)
    closing.current = false
    paused.current = false
    if (!navigator.mediaDevices?.getUserMedia || typeof WebSocket === 'undefined') {
      setError('Voice conversation is not supported here (it needs a secure page and microphone access).')
      return
    }
    setState('connecting')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
    } catch (err) {
      fail(describeMicError(err))
      return
    }
    if (closing.current) {
      stream.getTracks().forEach((t) => t.stop()) // ended while the permission prompt was open
      return
    }
    media.current = stream
    const sock = new WebSocket(socketUrl())
    sock.binaryType = 'arraybuffer'
    socket.current = sock
    sock.onopen = () => sock.send(JSON.stringify({ type: 'auth', initData: getInitData() }))
    sock.onerror = () => {
      if (!closing.current) fail('Could not connect to the voice service.')
    }
    sock.onclose = () => {
      if (!closing.current) fail('The voice connection closed.')
    }
    sock.onmessage = async (ev: MessageEvent) => {
      if (typeof ev.data !== 'string') return
      let msg: { type?: string; text?: string; final?: boolean; speech_final?: boolean; code?: string; message?: string }
      try {
        msg = JSON.parse(ev.data)
      } catch {
        return
      }
      switch (msg.type) {
        case 'ready':
          try {
            const cap = await startPcmCapture(stream, (pcm) => {
              if (sock.readyState === 1 && !paused.current) sock.send(pcm)
            })
            if (closing.current) {
              cap.stop()
              return
            }
            cap.setPaused(paused.current)
            capture.current = cap
            setState('listening')
          } catch (err) {
            fail(describeMicError(err))
          }
          return
        case 'speech_started':
          if (!paused.current) handlersRef.current.onActivity?.()
          return
        case 'transcript': {
          if (paused.current) return
          handlersRef.current.onActivity?.()
          const text = (msg.text ?? '').trim()
          if (msg.final) {
            if (text) finals.current.push(text)
            setInterim(finals.current.join(' '))
            if (msg.speech_final) flush()
          } else if (text) {
            setInterim([...finals.current, text].join(' '))
          }
          return
        }
        case 'utterance_end':
          flush()
          return
        case 'error':
          fail(SERVER_ERRORS[msg.code ?? ''] ?? msg.message ?? 'The voice service reported an error.')
          return
      }
    }
  }, [fail, flush])

  useEffect(
    () => () => {
      closing.current = true
      cleanup()
    },
    [cleanup],
  )

  return { state, interim, error, start, stop, setPaused }
}
