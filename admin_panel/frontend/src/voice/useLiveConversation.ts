import { useCallback, useEffect, useRef, useState } from 'react'
import { describeMicError } from '../hooks/useVoiceRecorder'
import { getInitData } from '../telegram/initData'
import { startPcmCapture } from './pcmCapture'
import type { PcmCapture } from './pcmCapture'
import { createPcmPlayer, pcmPlayerAvailable } from './pcmPlayer'
import type { PcmPlayer } from './pcmPlayer'
import { ERRORS, NOTICES, PROVIDER_DOWN, TAP_WINDOW_MS, liveSocketUrl } from './liveConfig'
import { MIC_CONSTRAINTS } from './streamConfig'

export { TAP_WINDOW_MS } from './liveConfig'

export type LivePhase = 'connecting' | 'listening' | 'thinking' | 'speaking'

/**
 * The speech-to-speech conversation: the microphone streams to the server's live voice model, its voice comes back and
 * plays. Half-duplex: while the model's voice plays (or the microphone is muted) nothing is sent, so the model never
 * hears itself, and a tap on the overlay cuts the model off and listens again. The microphone itself is opened once
 * and kept for the whole conversation: reopening it after every reply made Telegram ask for the permission every time
 * (Laris, 2026-11-10). The cost: a phone may keep its call audio mode (quieter playback) while the microphone is open.
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
  const [providerDown, setProviderDown] = useState(false) // the last conversation ended because the live voice failed
  const [elapsedMs, setElapsedMs] = useState(0)
  const socket = useRef<WebSocket | null>(null)
  const player = useRef<PcmPlayer | null>(null)
  const media = useRef<MediaStream | null>(null)
  const capture = useRef<PcmCapture | null>(null)
  const micGen = useRef(0) // a release makes a still pending microphone request stale
  const activeRef = useRef(false)
  const readyRef = useRef(false)
  const mutedRef = useRef(false)
  const playingRef = useRef(false)
  const lastTap = useRef(0)
  const micOn = useRef(false) // what the server was last told about the microphone
  const sending = useRef(false) // the microphone's audio goes to the server right now

  /** Tell the server the microphone opened or closed (the provider may need an end-of-stream marker). */
  const signalMic = useCallback((on: boolean) => {
    micOn.current = on
    const sock = socket.current
    if (sock && sock.readyState === 1) sock.send(JSON.stringify({ type: 'mic', on }))
  }, [])

  /** Send the microphone's audio only when the server is ready, the microphone is not muted and no voice plays. */
  const updateSending = useCallback(() => {
    const on = activeRef.current && readyRef.current && !mutedRef.current && !playingRef.current && !!capture.current
    sending.current = on
    capture.current?.setPaused(!on)
    if (on !== micOn.current) signalMic(on)
  }, [signalMic])

  /** The end of the conversation: the only time the microphone is closed. */
  const releaseMic = useCallback(() => {
    micGen.current++
    sending.current = false
    if (media.current && micOn.current) signalMic(false)
    capture.current?.stop()
    capture.current = null
    media.current?.getTracks().forEach((t) => t.stop())
    media.current = null
  }, [signalMic])

  const finish = useCallback(
    (outcome: { error?: string; notice?: string; providerDown?: boolean } = {}) => {
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
      if (outcome.providerDown) setProviderDown(true)
    },
    [releaseMic],
  )

  const beginCapture = useCallback(
    async (stream: MediaStream, gen: number) => {
      try {
        const cap = await startPcmCapture(stream, (pcm) => {
          const sock = socket.current
          if (sending.current && sock && sock.readyState === 1) sock.send(pcm)
        })
        if (gen !== micGen.current) cap.stop()
        else {
          capture.current = cap
          updateSending()
        }
      } catch (err) {
        finish({ error: describeMicError(err) })
      }
    },
    [finish, updateSending],
  )

  const onPlaying = useCallback(
    (value: boolean) => {
      playingRef.current = value
      setPlaying(value)
      if (!value) setInterim('') // what was heard belongs to the turn that has just been answered
      updateSending() // the microphone stays open; only what is sent changes
    },
    [updateSending],
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
        updateSending() // the microphone was opened before the socket was ready
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
        else finish({ error: ERRORS[code] ?? String(msg.message ?? 'The live voice reported an error.'), providerDown: PROVIDER_DOWN.has(code) })
      }
    },
    [finish, updateSending],
  )

  const start = useCallback(async () => {
    if (activeRef.current) return
    setError(null)
    setNotice(null)
    setProviderDown(false)
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
    sock.onerror = () => finish({ error: 'The live voice connection failed.', providerDown: true })
    sock.onclose = () => finish({ error: 'The live voice connection closed.', providerDown: true })
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
    updateSending()
  }, [updateSending])

  useEffect(() => {
    if (!active) return
    const started = Date.now()
    setElapsedMs(0)
    const tick = setInterval(() => setElapsedMs(Date.now() - started), 1000)
    return () => clearInterval(tick)
  }, [active])

  useEffect(() => () => finish(), [finish])

  const phase: LivePhase = !ready ? 'connecting' : playing ? 'speaking' : serverState === 'thinking' ? 'thinking' : 'listening'
  return { active, phase, muted, interim, activity, error, notice, providerDown, elapsedMs, start, end: () => finish(), toggleMute, interrupt }
}
