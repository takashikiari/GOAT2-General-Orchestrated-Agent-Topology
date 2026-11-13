import { useCallback, useEffect, useRef, useState } from 'react'
import { createEarcon } from './earcon'
import type { Earcon } from './earcon'
import { createRemoteVoice, remoteVoiceAvailable } from './remoteVoice'
import type { RemoteSpeaking, RemoteVoice } from './remoteVoice'
import { pickVoice, speak, speechAvailable, toSpeakable } from './speech'

type WatchedTurn = { id: string; status: string; reply: string | null }

/**
 * A voice message's answer, out loud (Laris, 2026-11-13): while GOAT works on the turn, the "working" sound repeats;
 * when the turn is done, its reply is read with the server's Deepgram voice (the one fixed voice, ``/api/voice/speak/
 * stream``), or the browser's voice when that is not available. ``prime`` must be called inside the tap that sends the
 * message (browsers only allow sound after one). The voice layer keeps no memory: it only reads what GOAT answered.
 */
export function useVoiceReply(turns: WatchedTurn[]) {
  const remote = useRef<RemoteVoice | null>(null)
  const earcon = useRef<Earcon | null>(null)
  const pending = useRef<string | null>(null)
  const speaking = useRef<RemoteSpeaking | { cancel: () => void } | null>(null)
  const [phase, setPhase] = useState<'idle' | 'thinking' | 'speaking'>('idle')

  const prime = useCallback(() => {
    if (earcon.current === null) earcon.current = createEarcon()
    earcon.current.prime()
    if (remote.current === null && remoteVoiceAvailable()) remote.current = createRemoteVoice()
    remote.current?.prime()
  }, [])

  const stop = useCallback(() => {
    pending.current = null
    earcon.current?.stop()
    speaking.current?.cancel()
    speaking.current = null
    setPhase('idle')
  }, [])

  const say = useCallback(async (reply: string) => {
    earcon.current?.stop()
    const text = toSpeakable(reply)
    if (!text) return setPhase('idle')
    setPhase('speaking')
    if (remote.current) {
      const spoken = remote.current.speak(text)
      speaking.current = spoken
      const result = await spoken.done
      if (speaking.current !== spoken) return // stopped, or a newer reply took over
      speaking.current = null
      if (result === 'ended') return setPhase('idle')
    }
    if (speechAvailable()) {
      const voice = await pickVoice('ro-RO')
      const browser = speak(text, voice, 'ro-RO')
      speaking.current = browser
      await browser.done.catch(() => {})
      if (speaking.current === browser) speaking.current = null
    }
    setPhase('idle')
  }, [])

  /** The turn this voice message started: the working sound plays until its reply is read. */
  const expect = useCallback((turnId: string) => {
    speaking.current?.cancel()
    speaking.current = null
    pending.current = turnId
    earcon.current?.start()
    setPhase('thinking')
  }, [])

  useEffect(() => {
    const id = pending.current
    if (!id) return
    const turn = turns.find((t) => t.id === id)
    if (!turn) return
    if (turn.status === 'done') {
      pending.current = null
      void say(turn.reply ?? '')
    } else if (turn.status === 'error' || turn.status === 'cancelled') {
      pending.current = null
      earcon.current?.stop()
      setPhase('idle')
    }
  }, [turns, say])

  useEffect(
    () => () => {
      earcon.current?.close()
      speaking.current?.cancel()
      remote.current?.close()
    },
    [],
  )

  return { prime, expect, say, stop, phase }
}
