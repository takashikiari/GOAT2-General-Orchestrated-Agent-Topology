/**
 * The "working" sound of a voice message: while GOAT thinks (a few seconds), a short soft tone repeats so the silence
 * never reads as a dropped message (Laris, 2026-11-13). Made on the page with WebAudio — never synthesized by a voice,
 * never sent anywhere. ``prime`` must run inside a tap (browsers only allow sound after one).
 */
export const EARCON_EVERY_MS = 1500
const TONE_HZ = 392 // a low, calm G
const TONE_S = 0.14
const PEAK_GAIN = 0.06

type AudioContextCtor = typeof AudioContext
const audioContextCtor = (): AudioContextCtor | undefined =>
  typeof window === 'undefined' ? undefined : window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext

export type Earcon = { prime: () => void; start: () => void; stop: () => void; close: () => void; readonly playing: boolean }

export function createEarcon(makeContext: () => AudioContext | null = () => {
  const Ctor = audioContextCtor()
  return Ctor ? new Ctor() : null
}): Earcon {
  let ctx: AudioContext | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  const context = (): AudioContext | null => {
    if (!ctx) {
      try {
        ctx = makeContext()
      } catch {
        ctx = null
      }
    }
    return ctx
  }
  const tone = () => {
    const c = context()
    if (!c) return
    try {
      const osc = c.createOscillator()
      const gain = c.createGain()
      osc.type = 'sine'
      osc.frequency.value = TONE_HZ
      const t = c.currentTime
      gain.gain.setValueAtTime(0, t)
      gain.gain.linearRampToValueAtTime(PEAK_GAIN, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + TONE_S)
      osc.connect(gain)
      gain.connect(c.destination)
      osc.start(t)
      osc.stop(t + TONE_S + 0.02)
    } catch {
      /* no sound here: the visual "thinking" state still shows */
    }
  }
  return {
    prime() {
      const c = context()
      if (c && c.state === 'suspended') void c.resume().catch(() => {})
    },
    start() {
      if (timer !== null) return
      tone()
      timer = setInterval(tone, EARCON_EVERY_MS)
    },
    stop() {
      if (timer !== null) clearInterval(timer)
      timer = null
    },
    close() {
      if (timer !== null) clearInterval(timer)
      timer = null
      void ctx?.close().catch(() => {})
      ctx = null
    },
    get playing() {
      return timer !== null
    },
  }
}
