import { vi } from 'vitest'

/** A MediaRecorder / getUserMedia stand-in: jsdom has neither. Each `stop()` delivers one chunk, like the real thing. */
export class FakeRecorder {
  static supported = new Set<string>(['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'])
  static instances: FakeRecorder[] = []
  static isTypeSupported(type: string) {
    return FakeRecorder.supported.has(type)
  }
  mimeType: string
  state: 'inactive' | 'recording' = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  options?: { mimeType?: string }
  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.options = options
    this.mimeType = options?.mimeType ?? 'audio/webm'
    FakeRecorder.instances.push(this)
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob([new Uint8Array(2048)], { type: this.mimeType }) })
    this.onstop?.()
  }
}

export function installFakeMedia(opts: { getUserMedia?: () => Promise<unknown> } = {}) {
  FakeRecorder.supported = new Set(['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'])
  FakeRecorder.instances = []
  const track = { stop: vi.fn() }
  const stream = { getTracks: () => [track] }
  const getUserMedia = vi.fn(opts.getUserMedia ?? (async () => stream))
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
  return { getUserMedia, track, stream }
}

export function removeFakeMedia() {
  vi.stubGlobal('MediaRecorder', undefined)
  Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
}
