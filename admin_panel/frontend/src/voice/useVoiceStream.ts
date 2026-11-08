import { useCallback, useEffect, useRef, useState } from 'react'
import { describeMicError } from '../hooks/useVoiceRecorder'
import { getInitData } from '../telegram/initData'
import { startPcmCapture } from './pcmCapture'
import type { PcmCapture } from './pcmCapture'
import { END_OF_TURN_MS, MIC_CONSTRAINTS, SERVER_ERRORS, socketUrl } from './streamConfig'

export { END_OF_TURN_MS } from './streamConfig'

export type StreamState = 'idle' | 'connecting' | 'listening'
type Handlers = {
  /** What was said up to END_OF_TURN_MS of silence, as text. Not called while paused. */
  onUtterance: (text: string) => void
  /** Something was heard (speech started or words came in): used to reset the silence timer. */
  onActivity?: () => void
}

/**
 * The microphone streamed to the server's live speech-to-text (Deepgram, via our WebSocket proxy) and turned into
 * utterances. Pausing releases the microphone (and drops anything half-heard): the conversation does this while GOAT
 * thinks and speaks, so it never listens to itself, and so the phone leaves its call audio mode (an open microphone with
 * echo cancellation plays everything at the quiet call volume, 2026-11-08). The socket stays open meanwhile (the server
 * keeps Deepgram alive), and the microphone is opened again on resume.
 */
export function useVoiceStream(handlers: Handlers) {
  const [state, setState] = useState<StreamState>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const socket = useRef<WebSocket | null>(null)
  const media = useRef<MediaStream | null>(null)
  const capture = useRef<PcmCapture | null>(null)
  const finals = useRef<string[]>([])
  const turnTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const paused = useRef(false)
  const opening = useRef(false)
  const closing = useRef(false)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const clearTurnTimer = useCallback(() => {
    if (turnTimer.current !== null) clearTimeout(turnTimer.current)
    turnTimer.current = null
  }, [])

  const releaseMic = useCallback(() => {
    capture.current?.stop()
    capture.current = null
    media.current?.getTracks().forEach((t) => t.stop())
    media.current = null
  }, [])

  const cleanup = useCallback(() => {
    clearTurnTimer()
    releaseMic()
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
  }, [clearTurnTimer, releaseMic])

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
    clearTurnTimer()
    const text = finals.current.join(' ').trim()
    finals.current = []
    setInterim('')
    if (text && !paused.current) handlersRef.current.onUtterance(text)
  }, [clearTurnTimer])

  /** Something was heard: the turn ends END_OF_TURN_MS after the last of it, if words were finalised by then. */
  const armTurnTimer = useCallback(() => {
    clearTurnTimer()
    turnTimer.current = setTimeout(() => {
      turnTimer.current = null
      if (finals.current.length) flush()
    }, END_OF_TURN_MS)
  }, [clearTurnTimer, flush])

  /** The microphone (again) and its PCM capture into the open socket; false (after reporting it) when that failed. */
  const openMic = useCallback(
    async (sock: WebSocket, existing?: MediaStream): Promise<boolean> => {
      if (opening.current || capture.current) return true
      opening.current = true
      try {
        let stream = existing ?? media.current
        if (!stream) {
          try {
            stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS)
          } catch (err) {
            fail(describeMicError(err))
            return false
          }
        }
        if (closing.current || paused.current || socket.current !== sock) {
          stream.getTracks().forEach((t) => t.stop()) // paused or ended while the microphone was opening
          if (media.current === stream) media.current = null
          return !closing.current
        }
        media.current = stream
        try {
          const cap = await startPcmCapture(stream, (pcm) => {
            if (sock.readyState === 1 && !paused.current) sock.send(pcm)
          })
          if (closing.current || paused.current || socket.current !== sock) {
            cap.stop()
            if (paused.current && !closing.current) releaseMic()
            return !closing.current
          }
          capture.current = cap
          return true
        } catch (err) {
          fail(describeMicError(err))
          return false
        }
      } finally {
        opening.current = false
      }
    },
    [fail, releaseMic],
  )

  const setPaused = useCallback(
    (p: boolean) => {
      const was = paused.current
      paused.current = p
      if (p) {
        clearTurnTimer()
        finals.current = []
        setInterim('')
        releaseMic()
      } else if (was) {
        const sock = socket.current
        if (sock && sock.readyState === 1 && !closing.current) void openMic(sock)
      }
    },
    [clearTurnTimer, openMic, releaseMic],
  )

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
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS)
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
          if (paused.current) {
            releaseMic() // paused before the server was ready: the microphone opens on resume
            setState('listening')
          } else if (await openMic(sock, stream)) {
            if (!closing.current) setState('listening')
          }
          return
        case 'speech_started':
          if (paused.current) return
          handlersRef.current.onActivity?.()
          if (finals.current.length) armTurnTimer()
          return
        case 'transcript': {
          if (paused.current) return
          handlersRef.current.onActivity?.()
          const text = (msg.text ?? '').trim()
          if (msg.final) {
            if (text) finals.current.push(text)
            setInterim(finals.current.join(' '))
          } else if (text) {
            setInterim([...finals.current, text].join(' '))
          }
          // a pause (speech_final, utterance_end) is not the end of the turn: only END_OF_TURN_MS of silence is
          if (text || finals.current.length) armTurnTimer()
          return
        }
        case 'utterance_end':
          if (!paused.current && finals.current.length) armTurnTimer()
          return
        case 'error':
          fail(SERVER_ERRORS[msg.code ?? ''] ?? msg.message ?? 'The voice service reported an error.')
          return
      }
    }
  }, [armTurnTimer, fail, openMic, releaseMic])

  useEffect(
    () => () => {
      closing.current = true
      cleanup()
    },
    [cleanup],
  )

  return { state, interim, error, start, stop, setPaused }
}
