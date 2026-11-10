/** Microphone → 16 kHz mono 16-bit PCM frames (what the server's streaming speech-to-text expects). */
export type PcmCapture = {
  /** While paused nothing is sent (used while GOAT thinks and speaks, so it never hears itself). */
  setPaused: (paused: boolean) => void
  /** Change the microphone gain while capturing (a value set at start through ``opts.gain``; kept in range). */
  setGain: (gain: number) => void
  stop: () => void
}

/** The microphone gain the page may apply in software (Laris found his voice too quiet in the live voice, 2026-11-10;
 * the browser's own auto gain control does not raise it enough). 1 = untouched. */
export const MIN_GAIN = 1
export const MAX_GAIN = 2.5

export function clampGain(gain: number): number {
  return Number.isFinite(gain) ? Math.min(MAX_GAIN, Math.max(MIN_GAIN, gain)) : 1
}

/**
 * The microphone's way into the capture. At gain 1 the source is used as it is (the Deepgram path does not ask for a
 * gain). Above 1: source → gain → limiter. The limiter is a DynamicsCompressor set hard (threshold -3 dBFS, no knee,
 * ratio 20, 2 ms attack): it leaves normal speech alone and flattens only the peaks the gain would push past full
 * scale, so a louder voice does not distort (the 16-bit conversion clamps whatever is left).
 */
export function buildInputChain(ctx: AudioContext, source: AudioNode, gain: number): { output: AudioNode; setGain: (g: number) => void } {
  const start = clampGain(gain)
  if (start === 1) return { output: source, setGain: () => {} }
  const amp = ctx.createGain()
  amp.gain.value = start
  const limiter = ctx.createDynamicsCompressor()
  limiter.threshold.value = -3
  limiter.knee.value = 0
  limiter.ratio.value = 20
  limiter.attack.value = 0.002
  limiter.release.value = 0.1
  source.connect(amp)
  amp.connect(limiter)
  return { output: limiter, setGain: (g: number) => { amp.gain.value = clampGain(g) } }
}

export const TARGET_RATE = 16000
/** 250 ms of audio per frame. */
export const FRAME_SAMPLES = 4000

// A worklet module as a string: no extra file to build or serve.
const WORKLET_SOURCE = `class PcmProcessor extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true }
}
registerProcessor('pcm-processor', PcmProcessor)`

/** Float samples at ``rate`` → 16 kHz 16-bit PCM (linear-interpolation resampling when the rate differs). */
export function toPcm16(samples: Float32Array, rate: number): Int16Array {
  const ratio = rate / TARGET_RATE
  const length = ratio === 1 ? samples.length : Math.floor(samples.length / ratio)
  const out = new Int16Array(length)
  for (let i = 0; i < length; i++) {
    let value: number
    if (ratio === 1) {
      value = samples[i]
    } else {
      const pos = i * ratio
      const left = Math.floor(pos)
      const right = Math.min(left + 1, samples.length - 1)
      const frac = pos - left
      value = samples[left] * (1 - frac) + samples[right] * frac
    }
    const clamped = Math.max(-1, Math.min(1, value))
    out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff
  }
  return out
}

function merge(chunks: Float32Array[], total: number): Float32Array {
  const out = new Float32Array(total)
  let at = 0
  for (const c of chunks) {
    out.set(c, at)
    at += c.length
  }
  return out
}

export async function startPcmCapture(
  stream: MediaStream,
  onFrame: (pcm: ArrayBuffer) => void,
  opts: { gain?: number } = {},
): Promise<PcmCapture> {
  let ctx: AudioContext
  try {
    ctx = new AudioContext({ sampleRate: TARGET_RATE })
  } catch {
    ctx = new AudioContext() // a browser that will not run at 16 kHz: we resample ourselves
  }
  const source = ctx.createMediaStreamSource(stream)
  const input = buildInputChain(ctx, source, opts.gain ?? 1)
  const need = Math.round((FRAME_SAMPLES * ctx.sampleRate) / TARGET_RATE)
  let paused = false
  let chunks: Float32Array[] = []
  let total = 0
  const push = (chunk: Float32Array) => {
    if (paused) return
    chunks.push(chunk)
    total += chunk.length
    if (total >= need) {
      const merged = merge(chunks, total)
      chunks = []
      total = 0
      onFrame(toPcm16(merged, ctx.sampleRate).buffer as ArrayBuffer)
    }
  }

  let disconnect: () => void
  if (ctx.audioWorklet) {
    const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }))
    try {
      await ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    const node = new AudioWorkletNode(ctx, 'pcm-processor')
    node.port.onmessage = (e: MessageEvent<Float32Array>) => push(e.data)
    const silent = ctx.createGain() // keeps the node in the audio graph without playing anything
    silent.gain.value = 0
    input.output.connect(node)
    node.connect(silent)
    silent.connect(ctx.destination)
    disconnect = () => {
      node.port.onmessage = null
      node.disconnect()
      silent.disconnect()
    }
  } else {
    const node = ctx.createScriptProcessor(4096, 1, 1) // old browsers without AudioWorklet
    node.onaudioprocess = (e) => push(new Float32Array(e.inputBuffer.getChannelData(0)))
    const silent = ctx.createGain()
    silent.gain.value = 0
    input.output.connect(node)
    node.connect(silent)
    silent.connect(ctx.destination)
    disconnect = () => {
      node.onaudioprocess = null
      node.disconnect()
      silent.disconnect()
    }
  }
  if (ctx.state === 'suspended') await ctx.resume()

  return {
    setGain: input.setGain,
    setPaused: (p) => {
      paused = p
      if (p) {
        chunks = []
        total = 0
      }
    },
    stop: () => {
      paused = true
      disconnect()
      source.disconnect()
      void ctx.close()
    },
  }
}
