import { useCallback, useEffect, useRef, useState } from 'react'
import { createRemoteVoice, remoteVoiceAvailable } from './remoteVoice'
import type { RemoteVoice } from './remoteVoice'
import { pickVoice, speak, speechAvailable, toSpeakable } from './speech'
import { useVoiceStream } from './useVoiceStream'

export type Phase = 'connecting' | 'listening' | 'thinking' | 'speaking'
/** Who reads GOAT's reply aloud: Deepgram's Romanian voice (through the panel), or the browser's own voice (free). */
export type VoiceKind = 'deepgram' | 'browser'
const VOICE_KEY = 'goat.voice'

function savedVoice(): VoiceKind {
  try {
    return window.localStorage.getItem(VOICE_KEY) === 'browser' ? 'browser' : 'deepgram'
  } catch {
    return 'deepgram'
  }
}
type TurnLike = { id: string; status: string; reply: string | null }

/** No speech for this long ends the conversation (the microphone and the streaming are billed per second). */
export const IDLE_MS = 90_000

/**
 * Voice to voice: listen (streamed speech-to-text) → send what was said to GOAT like a typed message → wait for its
 * reply → read the reply aloud → listen again. The microphone is paused while GOAT thinks and speaks.
 * ``submit`` sends a message and returns the id of the turn it started (null when it could not be sent);
 * ``turns`` is the page's list of turns, which this watches for the reply.
 */
export function useConversation({ turns, submit }: { turns: TurnLike[]; submit: (text: string) => Promise<string | null> }) {
  const [active, setActive] = useState(false)
  const [phase, setPhase] = useState<Phase>('connecting')
  const [muted, setMuted] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [voiceKind, setVoiceKindState] = useState<VoiceKind>(savedVoice)
  const voiceKindRef = useRef<VoiceKind>(voiceKind)
  const phaseRef = useRef<Phase>('connecting')
  const mutedRef = useRef(false)
  const activeRef = useRef(false)
  const pending = useRef<string | null>(null)
  const speaking = useRef<{ cancel: () => void } | null>(null)
  const remote = useRef<RemoteVoice | null>(null)
  const remoteDown = useRef(false)
  const idle = useRef<ReturnType<typeof setTimeout> | null>(null)
  const noVoiceTold = useRef(false)
  const onUtterance = useRef<(text: string) => void>(() => {})
  const onActivity = useRef<() => void>(() => {})

  const stream = useVoiceStream({
    onUtterance: (t) => onUtterance.current(t),
    onActivity: () => onActivity.current(),
  })

  const { start: streamStart, stop: streamStop, setPaused: streamSetPaused } = stream

  const goTo = useCallback((p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }, [])

  const clearIdle = useCallback(() => {
    if (idle.current) clearTimeout(idle.current)
    idle.current = null
  }, [])

  const finish = useCallback(
    (message?: string) => {
      clearIdle()
      speaking.current?.cancel()
      speaking.current = null
      pending.current = null
      activeRef.current = false
      streamStop()
      setActive(false)
      if (message) setNotice(message)
    },
    [clearIdle, streamStop],
  )

  const armIdle = useCallback(() => {
    clearIdle()
    idle.current = setTimeout(() => finish(`Conversation ended after ${IDLE_MS / 1000} seconds of silence.`), IDLE_MS)
  }, [clearIdle, finish])

  const resume = useCallback(() => {
    if (!activeRef.current) return
    goTo('listening')
    streamSetPaused(mutedRef.current)
    armIdle()
  }, [armIdle, goTo, streamSetPaused])

  onActivity.current = () => {
    if (phaseRef.current === 'listening' && activeRef.current) armIdle()
  }

  onUtterance.current = (text: string) => {
    if (!activeRef.current || phaseRef.current !== 'listening' || mutedRef.current) return
    goTo('thinking')
    streamSetPaused(true)
    clearIdle()
    void submit(text).then(
      (id) => {
        if (!activeRef.current) return
        if (id) pending.current = id
        else resume()
      },
      () => {
        setNotice('The message could not be sent.')
        resume()
      },
    )
  }

  const say = useCallback(
    async (reply: string) => {
      const text = toSpeakable(reply)
      if (!text) return resume()
      goTo('speaking')
      if (voiceKindRef.current === 'deepgram' && remote.current && !remoteDown.current) {
        const spoken = remote.current.speak(text)
        speaking.current = spoken
        const result = await spoken.done
        if (speaking.current === spoken) speaking.current = null
        if (!activeRef.current) return
        if (result === 'ended') {
          if (phaseRef.current === 'speaking') resume()
          return
        }
        remoteDown.current = true
        setNotice('The Deepgram voice is not available right now; using the browser voice.')
      }
      if (!speechAvailable()) {
        if (!noVoiceTold.current) {
          setNotice(
            remoteDown.current
              ? 'The Deepgram voice is not available right now and this browser cannot speak; GOAT answers in the chat.'
              : 'This browser cannot speak; GOAT answers in the chat.',
          )
        }
        noVoiceTold.current = true
        return resume()
      }
      const voice = await pickVoice('ro')
      if (!activeRef.current) return
      if (!voice) {
        if (!noVoiceTold.current) {
          setNotice(
            remoteDown.current
              ? 'The Deepgram voice is not available right now and no Romanian voice is installed on this device, so GOAT answers in the chat only.'
              : 'No Romanian voice is installed on this device, so GOAT answers in the chat only.',
          )
        }
        noVoiceTold.current = true
        return resume()
      }
      const speech = speak(text, voice, 'ro-RO')
      speaking.current = speech
      await speech.done
      if (speaking.current === speech) speaking.current = null
      if (activeRef.current && phaseRef.current === 'speaking') resume()
    },
    [goTo, resume],
  )

  // the reply of the turn we sent arrives through the page's turn list
  useEffect(() => {
    const id = pending.current
    if (!id || !activeRef.current) return
    const turn = turns.find((t) => t.id === id)
    if (!turn || (turn.status !== 'done' && turn.status !== 'error' && turn.status !== 'cancelled')) return
    pending.current = null
    if (turn.status === 'done' && turn.reply) void say(turn.reply)
    else resume()
  }, [turns, say, resume])

  // the stream went away by itself (permission, server, network): the conversation is over
  useEffect(() => {
    if (stream.error && activeRef.current) {
      clearIdle()
      speaking.current?.cancel()
      speaking.current = null
      pending.current = null
      activeRef.current = false
      setActive(false)
    }
  }, [stream.error, clearIdle])

  // first time the stream is up: start listening
  useEffect(() => {
    if (stream.state === 'listening' && activeRef.current && phaseRef.current === 'connecting') {
      goTo('listening')
      armIdle()
    }
  }, [stream.state, goTo, armIdle])

  useEffect(() => {
    if (!active) return
    const started = Date.now()
    setElapsedMs(0)
    const tick = setInterval(() => setElapsedMs(Date.now() - started), 1000)
    return () => clearInterval(tick)
  }, [active])

  useEffect(
    () => () => {
      clearIdle()
      speaking.current?.cancel()
    },
    [clearIdle],
  )

  const start = useCallback(() => {
    if (activeRef.current) return
    setNotice(null)
    noVoiceTold.current = false
    remoteDown.current = false
    // created and primed here, inside the tap that starts the conversation, so the browser lets it play later
    remote.current = remoteVoiceAvailable() ? createRemoteVoice() : null
    remote.current?.prime()
    mutedRef.current = false
    setMuted(false)
    activeRef.current = true
    setActive(true)
    goTo('connecting')
    void streamStart()
  }, [goTo, streamStart])

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current
    mutedRef.current = next
    setMuted(next)
    if (phaseRef.current === 'listening') streamSetPaused(next)
  }, [streamSetPaused])

  const setVoiceKind = useCallback((kind: VoiceKind) => {
    voiceKindRef.current = kind
    setVoiceKindState(kind)
    try {
      window.localStorage.setItem(VOICE_KEY, kind)
    } catch {
      /* private mode: the choice just lasts until the page is closed */
    }
  }, [])

  const interrupt = useCallback(() => {
    if (phaseRef.current === 'speaking') speaking.current?.cancel()
  }, [])

  return {
    active,
    phase,
    muted,
    interim: stream.interim,
    error: stream.error,
    notice,
    elapsedMs,
    voiceKind,
    setVoiceKind,
    start,
    end: () => finish(),
    toggleMute,
    interrupt,
  }
}
