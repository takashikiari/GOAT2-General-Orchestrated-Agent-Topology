import { getInitData } from '../telegram/initData'

/**
 * GOAT's reply spoken by Deepgram's Romanian voice, streamed through the panel: the text goes out on a WebSocket, 16-bit
 * PCM comes back as it is generated and is played gaplessly with Web Audio, so the first words are heard after a
 * fraction of a second instead of after the whole reply has been synthesised.
 */

/** Nothing heard this long after the text was sent: give up (the caller falls back to another voice). */
export const FIRST_AUDIO_MS = 15_000
/** Played this long before the first chunk, so the next ones arrive in time. */
const LEAD_S = 0.12

/** ``ended``: it was read (or cancelled, or broke off part-way). ``unavailable``: no sound at all, so use another voice. */
export type RemoteSpeaking = { done: Promise<'ended' | 'unavailable'>; cancel: () => void }
export type RemoteVoice = { prime: () => void; speak: (text: string) => RemoteSpeaking; close: () => void }

type AudioContextCtor = typeof AudioContext
const audioContextCtor = (): AudioContextCtor | undefined =>
  typeof window === 'undefined' ? undefined : window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext

export function remoteVoiceAvailable(): boolean {
  return typeof WebSocket !== 'undefined' && audioContextCtor() !== undefined
}

function socketUrl(): string {
  const url = new URL('/api/voice/speak/stream', window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/**
 * ``prime`` must be called inside the tap that starts the conversation: the browser only lets a page make sound after
 * one, and a context created (and resumed) there stays allowed for the later replies. One voice serves every
 * conversation of the page (iOS only allows a few audio contexts at once); ``close`` releases it.
 */
export function createRemoteVoice(openSocket: () => WebSocket = () => new WebSocket(socketUrl())): RemoteVoice {
  let ctx: AudioContext | null = null
  const context = (): AudioContext => {
    if (!ctx) {
      const Ctor = audioContextCtor()!
      ctx = new Ctor()
    }
    return ctx
  }

  return {
    prime() {
      try {
        const c = context()
        if (c.state === 'suspended') void c.resume().catch(() => {})
      } catch {
        /* no audio here: speak() will say "unavailable" and the browser voice takes over */
      }
    },

    close() {
      const c = ctx
      ctx = null
      void c?.close().catch(() => {})
    },

    speak(text) {
      let c: AudioContext
      let socket: WebSocket
      try {
        c = context()
        if (c.state === 'suspended') void c.resume().catch(() => {})
        socket = openSocket()
      } catch {
        return { done: Promise.resolve('unavailable'), cancel: () => {} }
      }
      socket.binaryType = 'arraybuffer'

      let resolve: (r: 'ended' | 'unavailable') => void = () => {}
      const done = new Promise<'ended' | 'unavailable'>((r) => (resolve = r))
      const playing = new Set<AudioBufferSourceNode>()
      let rate = 24000
      let nextTime = 0
      let heard = false // any audio has been scheduled
      let serverDone = false
      let settled = false
      let carry: number | null = null // a frame can end in the middle of a 16-bit sample
      const firstAudioTimer = setTimeout(() => finish(), FIRST_AUDIO_MS)

      function finish() {
        if (settled) return
        settled = true
        clearTimeout(firstAudioTimer)
        try {
          socket.close()
        } catch {
          /* already closed */
        }
        resolve(heard ? 'ended' : 'unavailable')
      }
      const maybeFinish = () => {
        if (serverDone && playing.size === 0) finish()
      }

      const play = (buffer: ArrayBuffer) => {
        let bytes = new Uint8Array(buffer)
        if (carry !== null) {
          const joined = new Uint8Array(bytes.length + 1)
          joined[0] = carry
          joined.set(bytes, 1)
          bytes = joined
          carry = null
        }
        if (bytes.length % 2 === 1) {
          carry = bytes[bytes.length - 1]
          bytes = bytes.subarray(0, bytes.length - 1)
        }
        const samples = bytes.length / 2
        if (samples === 0) return
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.length)
        const audio = new Float32Array(samples)
        for (let i = 0; i < samples; i++) audio[i] = view.getInt16(i * 2, true) / 32768
        const chunk = c.createBuffer(1, samples, rate)
        chunk.copyToChannel(audio, 0)
        const source = c.createBufferSource()
        source.buffer = chunk
        source.connect(c.destination)
        nextTime = Math.max(nextTime, c.currentTime + (heard ? 0 : LEAD_S))
        source.onended = () => {
          playing.delete(source)
          maybeFinish()
        }
        playing.add(source)
        source.start(nextTime)
        nextTime += chunk.duration
        if (!heard) clearTimeout(firstAudioTimer)
        heard = true
      }

      socket.onopen = () => {
        socket.send(JSON.stringify({ type: 'auth', initData: getInitData() }))
        socket.send(JSON.stringify({ type: 'speak', text }))
      }
      socket.onmessage = (event) => {
        if (settled) return
        if (typeof event.data !== 'string') return play(event.data as ArrayBuffer)
        try {
          const msg = JSON.parse(event.data) as { type?: string; sample_rate?: number }
          if (msg.type === 'ready' && typeof msg.sample_rate === 'number') rate = msg.sample_rate
          else if (msg.type === 'done') {
            serverDone = true
            maybeFinish()
          } else if (msg.type === 'error') finish()
        } catch {
          /* not a message we know */
        }
      }
      // the connection went away: what is already scheduled plays out, and if nothing was, another voice takes over
      socket.onerror = () => {
        if (!heard) finish()
      }
      socket.onclose = () => {
        serverDone = true
        maybeFinish()
      }

      return {
        done,
        cancel() {
          if (settled) return
          try {
            if (socket.readyState === 1) socket.send(JSON.stringify({ type: 'cancel' }))
          } catch {
            /* closing anyway */
          }
          playing.forEach((s) => {
            s.onended = null
            try {
              s.stop()
            } catch {
              /* not started */
            }
          })
          playing.clear()
          heard = true // cancelling is not a failure: do not fall back to another voice
          finish()
        },
      }
    },
  }
}
