import { afterEach, describe, expect, it, vi } from 'vitest'
import { pickVoice, speak, speechAvailable, splitSentences, toSpeakable } from './speech'

afterEach(() => vi.unstubAllGlobals())

describe('toSpeakable', () => {
  it('drops markdown, code, links and emoji', () => {
    const md = '## Rezumat\n**Gata:** am citit [raportul](https://x.example/r) și `repo_git`.\n```py\nprint(1)\n```\n- primul punct\n- al doilea 🎉'
    const out = toSpeakable(md)
    expect(out).not.toMatch(/[#*`\[\]]|https?:|print\(|🎉/)
    expect(out).toContain('Gata: am citit raportul și repo_git.')
    expect(out).toContain('primul punct. al doilea.')
  })

  it('keeps a short reply as it is and cuts a long one at a sentence with a pointer to the chat', () => {
    expect(toSpeakable('Totul e în regulă.')).toBe('Totul e în regulă.')
    const long = Array.from({ length: 60 }, (_, i) => `Propoziția numărul ${i} e despre ceva.`).join(' ')
    const out = toSpeakable(long)
    expect(out.length).toBeLessThan(760)
    expect(out.endsWith('Restul este în chat.')).toBe(true)
    expect(out).toMatch(/\. Restul este în chat\.$/)
  })

  it('gives nothing to say for code only', () => {
    expect(toSpeakable('```\nonly code\n```')).toBe('')
  })
})

describe('splitSentences', () => {
  it('splits at sentence ends into pieces of bounded size', () => {
    const text = Array.from({ length: 30 }, (_, i) => `Aceasta este propoziția ${i}.`).join(' ')
    const pieces = splitSentences(text, 100)
    expect(pieces.length).toBeGreaterThan(5)
    expect(pieces.every((p) => p.length <= 150)).toBe(true)
    expect(pieces.join(' ').replace(/\s+/g, ' ')).toBe(text)
  })

  it('breaks one very long sentence at spaces', () => {
    const pieces = splitSentences(`${'cuvânt '.repeat(120)}final`, 100)
    expect(pieces.length).toBeGreaterThan(3)
    expect(pieces.every((p) => p.length <= 160)).toBe(true)
  })
})

function stubSynth(voices: { lang: string; name: string }[], opts: { fail?: boolean } = {}) {
  const spoken: { text: string; lang: string; voice: unknown }[] = []
  const cancel = vi.fn()
  vi.stubGlobal('SpeechSynthesisUtterance', class { text: string; lang = ''; voice: unknown = null; onend: (() => void) | null = null; onerror: (() => void) | null = null; constructor(t: string) { this.text = t } })
  const synth = {
    getVoices: () => voices,
    speak: (u: { text: string; lang: string; voice: unknown; onend: (() => void) | null; onerror: (() => void) | null }) => {
      spoken.push({ text: u.text, lang: u.lang, voice: u.voice })
      setTimeout(() => (opts.fail ? u.onerror?.() : u.onend?.()), 0)
    },
    cancel, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }
  Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true })
  return { spoken, cancel }
}

describe('speaking', () => {
  it('finds a Romanian voice, or says there is none', async () => {
    stubSynth([{ lang: 'en-US', name: 'a' }, { lang: 'ro-RO', name: 'Ioana' }])
    expect(speechAvailable()).toBe(true)
    expect((await pickVoice('ro'))?.name).toBe('Ioana')
    stubSynth([{ lang: 'en-US', name: 'a' }])
    expect(await pickVoice('ro')).toBeNull()
  })

  it('reads the pieces in order and resolves at the end, with the chosen voice', async () => {
    const { spoken } = stubSynth([{ lang: 'ro-RO', name: 'Ioana' }])
    const voice = (await pickVoice('ro')) as SpeechSynthesisVoice
    await speak('Prima propoziție. A doua propoziție.', voice, 'ro-RO').done
    expect(spoken.map((s) => s.text).join(' ')).toBe('Prima propoziție. A doua propoziție.')
    expect(spoken.every((s) => s.lang === 'ro-RO' && s.voice === voice)).toBe(true)
  })

  it('cancel stops the rest and still resolves; an engine error resolves too', async () => {
    const { spoken, cancel } = stubSynth([{ lang: 'ro-RO', name: 'Ioana' }])
    const long = Array.from({ length: 10 }, (_, i) => `Propoziția ${i} este destul de lungă ca să facă piese separate. `.repeat(3)).join('')
    const speaking = speak(long, null, 'ro-RO')
    speaking.cancel()
    await speaking.done
    expect(cancel).toHaveBeenCalled()
    expect(spoken.length).toBeLessThan(3)
    stubSynth([], { fail: true })
    await speak('Ceva.', null, 'ro-RO').done
  })
})
