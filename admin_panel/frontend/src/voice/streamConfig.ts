/** Settings of the live speech-to-text stream (useVoiceStream): turn ending, microphone, server errors, socket. */

/**
 * Silence after the last word before what was said is handed over as one message. Deepgram finalises a segment after
 * 400 ms without speech, which cut Laris off at every breath (2026-11-08): the end of the turn is decided here instead,
 * and any new word or sound restarts the wait.
 */
export const END_OF_TURN_MS = 2500

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
}

/** What the server's error codes mean to the person using the page. */
export const SERVER_ERRORS: Record<string, string> = {
  unauthorized: 'Session expired — reopen the panel from the Telegram menu button.',
  not_configured: 'Voice conversation is not set up on the server yet (the Deepgram key is missing).',
  upstream_auth: 'The speech recognition service rejected the server key.',
  upstream: 'The speech recognition service is not reachable right now.',
  time_limit: 'The conversation reached its 30 minute limit.',
  frame_too_large: 'The audio could not be sent (frame too large).',
}

export function socketUrl(): string {
  const url = new URL('/api/voice/stream', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}
