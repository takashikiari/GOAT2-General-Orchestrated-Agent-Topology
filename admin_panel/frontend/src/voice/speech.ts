/** GOAT's reply spoken with the browser's own voice (free; Deepgram's voices do not speak Romanian). */

const SPOKEN_MAX = 700
const CHUNK_MAX = 200
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu

/** The reply as it should be read aloud: no markdown, code, links or emoji, and not longer than a few sentences. */
export function toSpeakable(text: string): string {
  let t = text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(EMOJI, ' ')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|~~|\*)/g, '')
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, '$1$2') // _italic_ but not repo_git
  // a line without closing punctuation becomes its own sentence, so list items are not read as one run-on
  t = t
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => (/[.!?…:;]$/.test(line) ? line : `${line}.`))
    .join(' ')
  t = t.replace(/\s+/g, ' ').trim()
  if (t.length <= SPOKEN_MAX) return t
  const cut = t.slice(0, SPOKEN_MAX)
  const end = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '))
  return `${(end > 200 ? cut.slice(0, end + 1) : cut).trim()} Restul este în chat.`
}

/** Pieces short enough for the browser to read without cutting itself off (some stop after ~15 s of one utterance). */
export function splitSentences(text: string, maxLen = CHUNK_MAX): string[] {
  const sentences = text.match(/[^.!?…]+[.!?…]*\s*/g) ?? [text]
  const out: string[] = []
  let current = ''
  for (const s of sentences) {
    if (current && (current + s).length > maxLen) {
      out.push(current.trim())
      current = ''
    }
    current += s
    while (current.length > maxLen * 1.5) {
      const at = current.lastIndexOf(' ', maxLen)
      const pos = at > 40 ? at : maxLen
      out.push(current.slice(0, pos).trim())
      current = current.slice(pos)
    }
  }
  if (current.trim()) out.push(current.trim())
  return out.filter(Boolean)
}

export function speechAvailable(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'
}

/** A voice for ``lang`` ("ro"), waiting a moment for the browser to load its list. null when the device has none. */
export async function pickVoice(lang: string): Promise<SpeechSynthesisVoice | null> {
  if (!speechAvailable()) return null
  const find = () => window.speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith(lang.toLowerCase())) ?? null
  const now = find()
  if (now || window.speechSynthesis.getVoices().length > 0) return now
  await new Promise<void>((resolve) => {
    const done = () => {
      window.speechSynthesis.removeEventListener?.('voiceschanged', done)
      resolve()
    }
    window.speechSynthesis.addEventListener?.('voiceschanged', done)
    setTimeout(done, 1000)
  })
  return find()
}

export type Speaking = { done: Promise<void>; cancel: () => void }

/** Read ``text`` aloud piece by piece. ``done`` resolves when it ends, is cancelled, or fails (it never rejects). */
export function speak(text: string, voice: SpeechSynthesisVoice | null, lang: string): Speaking {
  const pieces = splitSentences(text)
  let cancelled = false
  const done = new Promise<void>((resolve) => {
    let i = 0
    const next = () => {
      if (cancelled || i >= pieces.length) {
        resolve()
        return
      }
      const u = new SpeechSynthesisUtterance(pieces[i++])
      u.lang = voice?.lang ?? lang
      if (voice) u.voice = voice
      u.onend = next
      u.onerror = () => resolve()
      window.speechSynthesis.speak(u)
    }
    next()
  })
  return {
    done,
    cancel: () => {
      cancelled = true
      window.speechSynthesis.cancel()
    },
  }
}
