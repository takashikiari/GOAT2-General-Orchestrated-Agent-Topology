import { useCallback, useEffect, useRef, useState } from 'react'

export type VoiceState = 'idle' | 'recording' | 'sending'
export type VoiceClip = { blob: Blob; durationMs: number; mimeType: string; limitReached: boolean }

/** The longest recording; the server accepts a little more (65 s) to allow for timer and stop latency. */
export const VOICE_MAX_MS = 60_000
/** Anything shorter is treated as an accidental tap and is not uploaded (the server refuses it too). */
export const VOICE_MIN_MS = 300
const TICK_MS = 200
/** First one the browser can record wins: Chrome, Android and desktop give webm/opus, Safari and iOS only mp4. */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

export function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') return undefined
  return MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m))
}

export function voiceSupported(): boolean {
  return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

/** A permission or device problem in words the user can act on (never swallowed, never a bare exception name). */
export function describeMicError(err: unknown): string {
  const name = err && typeof err === 'object' && 'name' in err ? String((err as { name: unknown }).name) : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone permission denied. Allow microphone access for this page (browser or Telegram settings) and try again.'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No microphone found on this device.'
  if (name === 'NotReadableError' || name === 'AbortError') {
    return 'The microphone is busy or unavailable (another app may be using it).'
  }
  return err instanceof Error ? `Could not start recording: ${err.message}` : 'Could not start recording.'
}

/**
 * Record one voice message with MediaRecorder: tap to start (asks for the microphone), tap again to stop, or it stops by
 * itself at VOICE_MAX_MS. The finished clip goes to ``onRecorded``; the state is "sending" until that settles.
 * The microphone is released as soon as recording ends, and nothing is uploaded when the page goes away.
 */
export function useVoiceRecorder(onRecorded: (clip: VoiceClip) => Promise<void> | void) {
  const [state, setState] = useState<VoiceState>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const startedAt = useRef(0)
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null)
  const limit = useRef<ReturnType<typeof setTimeout> | null>(null)
  const discard = useRef(false)
  const hitLimit = useRef(false)
  const mounted = useRef(true)
  const onRecordedRef = useRef(onRecorded)
  onRecordedRef.current = onRecorded

  const release = useCallback(() => {
    if (ticker.current) clearInterval(ticker.current)
    if (limit.current) clearTimeout(limit.current)
    ticker.current = null
    limit.current = null
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
  }, [])

  const handleStop = useCallback(
    async (rec: MediaRecorder) => {
      const durationMs = Math.min(Date.now() - startedAt.current, VOICE_MAX_MS)
      const mimeType = rec.mimeType || pickMimeType() || 'audio/webm'
      const blob = new Blob(chunks.current, { type: mimeType })
      release()
      recorder.current = null
      if (discard.current || !mounted.current) {
        if (mounted.current) setState('idle')
        return
      }
      if (durationMs < VOICE_MIN_MS || blob.size === 0) {
        setState('idle')
        setError('That was too short — tap the mic, speak, and tap it again to stop.')
        return
      }
      setElapsedMs(durationMs)
      setState('sending')
      try {
        await onRecordedRef.current({ blob, durationMs, mimeType, limitReached: hitLimit.current })
      } catch (err) {
        if (mounted.current) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (mounted.current) setState('idle')
      }
    },
    [release],
  )

  const start = useCallback(async () => {
    setError(null)
    if (!voiceSupported()) {
      setError('Voice recording is not supported here (it needs a secure page and a browser with microphone access).')
      return
    }
    let media: MediaStream
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      setError(describeMicError(err))
      return
    }
    stream.current = media
    let rec: MediaRecorder
    const mime = pickMimeType()
    try {
      rec = mime ? new MediaRecorder(media, { mimeType: mime }) : new MediaRecorder(media)
    } catch {
      try {
        rec = new MediaRecorder(media)
      } catch (err) {
        release()
        setError(describeMicError(err))
        return
      }
    }
    chunks.current = []
    discard.current = false
    hitLimit.current = false
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.current.push(e.data)
    }
    rec.onerror = () => {
      discard.current = true
      release()
      recorder.current = null
      if (mounted.current) {
        setState('idle')
        setError('The recording failed. Try again.')
      }
    }
    rec.onstop = () => void handleStop(rec)
    recorder.current = rec
    rec.start()
    startedAt.current = Date.now()
    setElapsedMs(0)
    setState('recording')
    ticker.current = setInterval(() => setElapsedMs(Math.min(Date.now() - startedAt.current, VOICE_MAX_MS)), TICK_MS)
    limit.current = setTimeout(() => {
      hitLimit.current = true
      if (rec.state !== 'inactive') rec.stop()
    }, VOICE_MAX_MS)
  }, [handleStop, release])

  const toggle = useCallback(() => {
    if (state === 'recording') recorder.current?.stop()
    else if (state === 'idle') void start()
  }, [state, start])

  const clearError = useCallback(() => setError(null), [])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      discard.current = true
      const rec = recorder.current
      if (rec && rec.state !== 'inactive') rec.stop()
      release()
    }
  }, [release])

  return { state, elapsedMs, error, clearError, toggle, supported: voiceSupported() }
}
