import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import { useConversation } from './useConversation'
import { useLiveConversation } from './useLiveConversation'

/** Who talks in a voice conversation: the live speech-to-speech model, or the Deepgram / browser voice reading GOAT's text. */
export type VoiceMode = 'live' | 'deepgram' | 'browser'
const VOICE_KEY = 'goat.voice' // shared with useConversation, which only tells "browser" from everything else

function savedLive(): boolean {
  try {
    return window.localStorage.getItem(VOICE_KEY) === 'live'
  } catch {
    return false
  }
}

/**
 * The page's one voice conversation. "Live" is offered only when the server has a live voice (``/api/voice/live/status``);
 * otherwise this is the classic conversation (listen, send to GOAT, read the reply aloud) exactly as before.
 */
export function useVoiceConversation(args: Parameters<typeof useConversation>[0]) {
  const classic = useConversation(args)
  const live = useLiveConversation()
  const [liveAvailable, setLiveAvailable] = useState(false)
  const [liveChosen, setLiveChosen] = useState(savedLive)

  useEffect(() => {
    let gone = false
    apiGet<{ available?: boolean }>('/api/voice/live/status')
      .then((r) => !gone && setLiveAvailable(Boolean(r?.available)))
      .catch(() => {})
    return () => {
      gone = true
    }
  }, [])

  const voiceMode: VoiceMode = liveChosen && liveAvailable ? 'live' : classic.voiceKind
  const busy = classic.active || live.active

  /** live → deepgram → browser → live (the live step only when the server has it); not while a conversation runs. */
  const cycleVoice = useCallback(() => {
    if (busy) return
    const order: VoiceMode[] = liveAvailable ? ['live', 'deepgram', 'browser'] : ['deepgram', 'browser']
    const next = order[(order.indexOf(voiceMode) + 1) % order.length]
    setLiveChosen(next === 'live')
    if (next === 'live') {
      try {
        window.localStorage.setItem(VOICE_KEY, 'live')
      } catch {
        /* the choice lasts until the page closes */
      }
    } else classic.setVoiceKind(next) // also saves it, replacing "live"
  }, [busy, classic, liveAvailable, voiceMode])

  const current = voiceMode === 'live' ? live : { ...classic, activity: null as string | null }
  return {
    active: current.active,
    phase: current.phase,
    muted: current.muted,
    interim: current.interim,
    activity: current.activity,
    error: current.error,
    notice: current.notice,
    elapsedMs: current.elapsedMs,
    start: current.start,
    end: current.end,
    toggleMute: current.toggleMute,
    interrupt: current.interrupt,
    voiceMode,
    cycleVoice,
  }
}
