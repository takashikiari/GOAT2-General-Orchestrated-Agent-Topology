/** Settings of the live (speech-to-speech) conversation: what the server's codes mean, and where its socket is. */
import { SERVER_ERRORS } from './streamConfig'

/** An ``interrupted`` this soon after our own tap is the answer to that tap: the page already cleared, newer audio is not stale. */
export const TAP_WINDOW_MS = 1500

/** Server codes that are the normal end of a conversation, not a failure. */
export const NOTICES: Record<string, string> = {
  idle: 'Conversation ended after a stretch of silence.',
  time_limit: 'The conversation reached its time limit.',
}
export const ERRORS: Record<string, string> = {
  ...SERVER_ERRORS,
  not_configured: 'The live voice is not set up on the server yet.',
  chat_unavailable: "GOAT's chat is not available right now.",
  upstream: 'The live voice service is not reachable right now.',
  daily_limit: "Today's live voice minutes are used up.",
}
/** Server codes that mean the live voice itself is out of service (the page then talks with the Deepgram voice). */
export const PROVIDER_DOWN = new Set(['not_configured', 'upstream', 'daily_limit'])

export function liveSocketUrl(): string {
  const url = new URL('/api/voice/live', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** Software gain on the microphone in the live voice (Laris's voice came through too quiet, 2026-11-10): +5 dB, the
 * middle of the 1.5-2.0 range asked for; the limiter in ``pcmCapture`` keeps louder speech from distorting. */
export const LIVE_MIC_GAIN = 1.8

/**
 * The live voice's microphone, without the browser's call processing (echo cancellation, noise suppression, auto gain).
 * With that processing on, an open microphone puts a phone in its call audio mode: GOAT's voice plays at call volume,
 * possibly through the earpiece, so Laris had to hold the phone to his ear (2026-11-10). The live voice does not need
 * echo cancellation: the page sends nothing while the model's voice plays. LIVE_MIC_GAIN makes up for the missing
 * auto gain.
 */
export const LIVE_MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
}
