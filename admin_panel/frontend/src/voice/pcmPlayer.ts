/**
 * The model's voice: 16-bit mono PCM chunks played back to back with Web Audio. ``onPlaying`` says when sound starts
 * and when it has really stopped (a short tail after the last chunk, so a gap between two chunks does not flicker).
 * ``prime`` must be called inside the tap that starts the conversation: the browser only lets a page make sound after one.
 */

/** Played this long before the first chunk, so the next ones arrive in time. */
const LEAD_S = 0.1
/** Still "playing" this long after the last chunk ended. */
export const TAIL_MS = 250

export type PcmPlayer = {
  prime: () => void
  setRate: (rate: number) => void
  push: (buffer: ArrayBuffer) => void
  /** Stop everything queued or playing (the person tapped, or the model was cut off). */
  clear: () => void
  close: () => void
}

type AudioContextCtor = typeof AudioContext
const audioContextCtor = (): AudioContextCtor | undefined =>
  typeof window === 'undefined' ? undefined : window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext

export function pcmPlayerAvailable(): boolean {
  return audioContextCtor() !== undefined
}

export function createPcmPlayer(onPlaying: (playing: boolean) => void): PcmPlayer {
  let ctx: AudioContext | null = null
  let rate = 24000
  let nextTime = 0
  let carry: number | null = null // a frame can end in the middle of a 16-bit sample
  let playing = false
  let tail: ReturnType<typeof setTimeout> | null = null
  const sources = new Set<AudioBufferSourceNode>()

  const context = (): AudioContext => {
    if (!ctx) ctx = new (audioContextCtor()!)()
    return ctx
  }
  const setPlaying = (value: boolean) => {
    if (playing === value) return
    playing = value
    onPlaying(value)
  }
  const clearTail = () => {
    if (tail) clearTimeout(tail)
    tail = null
  }

  return {
    prime() {
      try {
        const c = context()
        if (c.state === 'suspended') void c.resume().catch(() => {})
      } catch {
        /* no audio on this device: the page tells the person when nothing ever plays */
      }
    },
    setRate(value) {
      rate = value
    },
    push(buffer) {
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
      const c = context()
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.length)
      const audio = new Float32Array(samples)
      for (let i = 0; i < samples; i++) audio[i] = view.getInt16(i * 2, true) / 32768
      const chunk = c.createBuffer(1, samples, rate)
      chunk.copyToChannel(audio, 0)
      const source = c.createBufferSource()
      source.buffer = chunk
      source.connect(c.destination)
      clearTail()
      nextTime = Math.max(nextTime, c.currentTime + (sources.size === 0 ? LEAD_S : 0))
      source.onended = () => {
        sources.delete(source)
        if (sources.size === 0) {
          clearTail()
          tail = setTimeout(() => setPlaying(false), TAIL_MS)
        }
      }
      sources.add(source)
      source.start(nextTime)
      nextTime += chunk.duration
      setPlaying(true)
    },
    clear() {
      clearTail()
      sources.forEach((s) => {
        s.onended = null
        try {
          s.stop()
        } catch {
          /* not started */
        }
      })
      sources.clear()
      carry = null
      nextTime = 0
      setPlaying(false)
    },
    close() {
      this.clear()
      const c = ctx
      ctx = null
      void c?.close().catch(() => {})
    },
  }
}
