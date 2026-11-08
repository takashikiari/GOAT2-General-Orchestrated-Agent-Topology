import { useCallback, useEffect, useRef, useState } from 'react'
import { describeMicError } from '../hooks/useVoiceRecorder'
import { getInitData } from '../telegram/initData'
import { startPcmCapture } from './pcmCapture'
import type { PcmCapture } from './pcmCapture'
import { createPcmPlayer, pcmPlayerAvailable } from './pcmPlayer'
import type { PcmPlayer } from './pcmPlayer'
import { MIC_CONSTRAINTS, SERVER_ERRORS } from './streamConfig'

export type LivePhase = 'connecting' | 'listening' | 'thinking' | 'speaking'

/** Server codes that are the normal end of a conversation, not a failure. */
/** An ``interrupted`` this soon after our own tap is the answer to that tap: the page already cleared, newer audio is not stale. */
export const TAP_WINDOW_MS = 1500

const NOTICES: Record<string, string> = {
  idle: 'Conversation ended after a stretch of silence.',
  time_limit: 'The conversation reached its time limit.',
}
const ERRORS: Record<string, string> = {
  ...SERVER_ERRORS,
  not_configured: 'The live voice is not set up on the server yet.',
  chat_unavailable: "GOAT's chat is not available right now.",
  upstream: 'The live voice service is not reachable right now.',
}

function liveSocketUrl(): string {
  const url = new URL('/api/voice/live', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/**
 * The speech-to-speech conversation: the microphone streams to the server's live voice model, its voice comes back and
 * plays. Half-duplex: the microphone is released while the model's voice plays (it never hears itself, and phones leave
 * their quiet call audio mode), and a tap on the overlay cuts the model off and listens again.
 */
export function useLiveConversation() {
  const [active, setActive] = useState(false)
  const [ready, setReady] = useState(false)
  const [serverState, setServerState] = useState('listening')
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [interim, setInterim] = useState('')
  const [activity, setActivity] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const socket = useRef<WebSocket | null>(null)
  const player = useRef<PcmPlayer | null>(null)
  const media = useRef<MediaStream | null>(null)
  const capture = useRef<PcmCapture | null>(null)
  const micGen = useRef(0) // a newer mic request (or a release) makes an older, still pending one stale
  const activeRef = useRef(false)
  const readyRef = useRef(false)
  const mutedRef = useRef(false)
  const playingRef = useRef(false)
  const lastTap = useRef(0)
  const micOn = useRef(false) // what the server was last told about the microphone

  /** Tell the server the microphone opened or closed (the provider may need an end-of-stream marker). */
  const signalMic = useCallback((on: boolean) => {
    micOn.current = on
    const sock = socket.current
    if (sock && sock.readyState === 1) sock.send(JSON.stringify({ type: 'mic', on }))
  }, [])

  const releaseMic = useCallback(() => {
    micGen.current++
    if (media.current) signalMic(false)
    capture.current?.stop()
    capture.current = null
    media.current?.getTracks().forEach((t) => t.stop())
    media.current = null
  }, [signalMic])

  const finish = useCallback(
    (outcome: { error?: string; notice?: string } = {}) => {
      if (!activeRef.current) return
      activeRef.current = false
      readyRef.current = false
      releaseMic()
      player.current?.close()
      player.current = null
      const sock = socket.current
      socket.current = null
      if (sock && sock.readyState <= 1) {
        try {
          if (sock.readyState === 1) sock.send(JSON.stringify({ type: 'close' }))
          sock.close()
        } catch {
          /* already closing */
        }
      }
      setActive(false)
      setReady(false)
      setPlaying(false)
      playingRef.current = false
      setInterim('')
      setActivity(null)
      if (outcome.error) setError(outcome.error)
      if (outcome.notice) setNotice(outcome.notice)
    },
    [releaseMic],
  )

  const beginCapture = useCallback(
    async (stream: MediaStream, gen: number) => {
      try {
        const cap = await startPcmCapture(stream, (pcm) => {
          const sock = socket.current
          if (sock && sock.readyState === 1) sock.send(pcm)
        })
        if (gen !== micGen.current) cap.stop()
        else {
          capture.current = cap
          signalMic(true)
        }
      } catch (err) {
        finish({ error: describeMicError(err) })
      }
    },
    [finish, signalMic],
  )

  /** Open the microphone again (it is released while the model speaks) unless it is muted, playing, or already open. */
  const openMic = useCallback(async () => {
    if (media.current || !activeRef.current || !readyRef.current || mutedRef.current || playingRef.current) return
    const gen = ++micGen.current
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS)
    } catch (err) {
      finish({ error: describeMicError(err) })
      return
    }
    if (gen !== micGen.current || !activeRef.current || playingRef.current || mutedRef.current) {
      stream.getTracks().forEach((t) => t.stop())
      return
    }
    media.current = stream
    await beginCapture(stream, gen)
  }, [beginCapture, finish])

  const onPlaying = useCallback(
    (value: boolean) => {
      playingRef.current = value
      setPlaying(value)
      if (value) releaseMic()
      else {
        setInterim('') // what was heard belongs to the turn that has just been answered
        void openMic()
      }
    },
    [openMic, releaseMic],
  )

  const onMessage = useCallback(
    (event: MessageEvent) => {
      if (!activeRef.current) return
      if (typeof event.data !== 'string') {
        player.current?.push(event.data as ArrayBuffer)
        return
      }
      let msg: { type?: string; [key: string]: unknown }
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }
      if (msg.type === 'ready') {
        player.current?.setRate(Number(msg.out_rate) || 24000)
        readyRef.current = true
        setReady(true)
        if (micOn.current) signalMic(true) // the microphone was opened before the socket was ready
        void openMic()
      } else if (msg.type === 'state') setServerState(String(msg.state))
      else if (msg.type === 'user_text') setInterim(String(msg.text ?? ''))
      else if (msg.type === 'tool') setActivity(msg.status === 'running' ? String(msg.name ?? '') : null)
      else if (msg.type === 'interrupted') {
        if (Date.now() - lastTap.current >= TAP_WINDOW_MS) player.current?.clear()
      }
      else if (msg.type === 'notice' && msg.code === 'go_away') setNotice('The voice service will disconnect soon.')
      else if (msg.type === 'error') {
        const code = String(msg.code ?? '')
        if (NOTICES[code]) finish({ notice: NOTICES[code] })
        else finish({ error: ERRORS[code] ?? String(msg.message ?? 'The live voice reported an error.') })
      }
    },
    [finish, openMic, signalMic],
  )

  const start = useCallback(async () => {
    if (activeRef.current) return
    setError(null)
    setNotice(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof WebSocket === 'undefined' || !pcmPlayerAvailable()) {
      setError('This browser cannot hold a live voice conversation.')
      return
    }
    activeRef.current = true
    readyRef.current = false
    mutedRef.current = false
    setMuted(false)
    setReady(false)
    setServerState('listening')
    setActive(true)
    // created and primed inside the tap that starts the conversation, so the browser lets it play later
    player.current = createPcmPlayer(onPlaying)
    player.current.prime()
    const gen = ++micGen.current
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS) // asked now, so a refusal shows at once
    } catch (err) {
      finish({ error: describeMicError(err) })
      return
    }
    if (!activeRef.current || gen !== micGen.current) {
      stream.getTracks().forEach((t) => t.stop())
      return
    }
    media.current = stream
    const sock = new WebSocket(liveSocketUrl())
    sock.binaryType = 'arraybuffer'
    socket.current = sock
    sock.onopen = () => sock.send(JSON.stringify({ type: 'auth', initData: getInitData() }))
    sock.onmessage = onMessage
    sock.onerror = () => finish({ error: 'The live voice connection failed.' })
    sock.onclose = () => finish({ error: 'The live voice connection closed.' })
    await beginCapture(stream, gen)
  }, [beginCapture, finish, onMessage, onPlaying])

  const interrupt = useCallback(() => {
    const sock = socket.current
    lastTap.current = Date.now()
    if (sock && sock.readyState === 1) sock.send(JSON.stringify({ type: 'interrupt' }))
    player.current?.clear()
  }, [])

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current
    mutedRef.current = next
    setMuted(next)
    if (next) releaseMic()
    else void openMic()
  }, [openMic, releaseMic])

  useEffect(() => {
    if (!active) return
    const started = Date.now()
    setElapsedMs(0)
    const tick = setInterval(() => setElapsedMs(Date.now() - started), 1000)
    return () => clearInterval(tick)
  }, [active])

  useEffect(() => () => finish(), [finish])

  const phase: LivePhase = !ready ? 'connecting' : playing ? 'speaking' : serverState === 'thinking' ? 'thinking' : 'listening'
  return { active, phase, muted, interim, activity, error, notice, elapsedMs, start, end: () => finish(), toggleMute, interrupt }
}
