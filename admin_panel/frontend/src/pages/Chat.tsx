import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, MouseEvent, RefObject } from 'react'
import { ApiError, apiGet, apiPost, apiPostForm } from '../api/client'
import { Banner } from '../components/Banner'
import { EndCallIcon, HeadsetIcon, MicIcon, MicOffIcon, SpeakerIcon } from '../components/icons'
import { VOICE_MAX_MS, useVoiceRecorder } from '../hooks/useVoiceRecorder'
import type { VoiceClip } from '../hooks/useVoiceRecorder'
import { useConversation } from '../voice/useConversation'
import type { Phase } from '../voice/useConversation'
import { usePolling } from '../hooks/usePolling'

type ChatState = { chat_id: string | null; busy: boolean; external_busy: boolean; active_turn_id: string | null }

type HistoryMessage = { timestamp: number; role: string; content: string }
/** The stored conversation WITHOUT the agent-coord digests (those are on the Agents page). */
type HistoryResponse = { chat_id: string; messages: HistoryMessage[]; warnings?: string[] }

type TurnStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'
type TurnEvent =
  | { seq: number; ts: number; type: 'queued' | 'started' | 'cancelled' }
  | { seq: number; ts: number; type: 'tool_start'; id: string; name: string; args: string }
  | { seq: number; ts: number; type: 'tool_end'; id: string; name: string; chars: number; ok: boolean; preview: string }
  | { seq: number; ts: number; type: 'reply'; text: string }
  | { seq: number; ts: number; type: 'error'; message: string }
type VoiceResponse = { ok: boolean; stub?: boolean; duration_ms: number; bytes: number; content_type: string; text: string }
type TurnView = { id: string; text: string; status: TurnStatus; reply: string | null; events: TurnEvent[] }

const POLL_TURN_MS = 700
const DONE_FALLBACK_MS = 30000
const isTerminal = (s: TurnStatus) => s === 'done' || s === 'error' || s === 'cancelled'
/** Within this many pixels of the end of the list counts as "at the bottom" (the user is following the conversation). */
const NEAR_BOTTOM_PX = 80

/**
 * Size the chat to the visible viewport (below whatever header the page has), so the PAGE never scrolls and
 * only the message list does. Re-fits on resize and when the on-screen keyboard changes the visual viewport.
 */
function useFillViewportHeight(ref: RefObject<HTMLElement>) {
  useLayoutEffect(() => {
    const fit = () => {
      const el = ref.current
      if (!el) return
      const viewport = window.visualViewport?.height ?? window.innerHeight
      const top = el.getBoundingClientRect().top + window.scrollY
      el.style.setProperty('--chat-h', `${Math.max(320, Math.floor(viewport - top - 16))}px`)
    }
    fit()
    window.addEventListener('resize', fit)
    window.visualViewport?.addEventListener('resize', fit)
    return () => {
      window.removeEventListener('resize', fit)
      window.visualViewport?.removeEventListener('resize', fit)
    }
  }, [ref])
}

const clock = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

function audioExtension(mime: string): string {
  const base = mime.split(';')[0].trim().toLowerCase()
  return base === 'audio/webm' ? 'webm' : base === 'audio/mp4' ? 'm4a' : base === 'audio/ogg' ? 'ogg' : 'bin'
}

/** Fallback for the height of the 2-row message box when it cannot be measured (tests). */
const INPUT_BASE_PX = 58

/**
 * Let the message box grow with what is typed, up to twice its resting (2-row) height, then scroll inside it.
 * Back to the resting height when it is emptied (after sending).
 */
function useAutosize(ref: RefObject<HTMLTextAreaElement>, value: string) {
  const base = useRef(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (!base.current) base.current = el.offsetHeight || INPUT_BASE_PX
    if (!value) {
      // empty: the resting height; a wrapped placeholder must not make the box taller
      el.style.height = `${base.current}px`
      el.style.overflowY = 'hidden'
      return
    }
    el.style.height = 'auto'
    const border = el.offsetHeight - el.clientHeight
    const natural = el.scrollHeight + border
    const max = base.current * 2
    el.style.height = `${Math.min(Math.max(natural, base.current), max)}px`
    el.style.overflowY = natural > max ? 'auto' : 'hidden'
  }, [ref, value])
}

/** Buttons must not take focus from the message box: on a phone that closes the keyboard and the page jumps. */
const keepFocus = (e: MouseEvent) => e.preventDefault()

function SendGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-5 w-5" aria-hidden="true">
      <path d="M12 19V5M5 12l7-7 7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function StopGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4" aria-hidden="true">
      <rect x="5" y="5" width="14" height="14" rx="2" />
    </svg>
  )
}

/** Assistant messages are stored as "[Tool calls]\n<summary>\n\n<reply>" when tools ran; show the two parts apart. */
function splitToolSummary(content: string): { tools: string | null; text: string } {
  const head = '[Tool calls]\n'
  if (content.startsWith(head)) {
    const end = content.indexOf('\n\n')
    if (end > 0) return { tools: content.slice(head.length, end), text: content.slice(end + 2) }
  }
  return { tools: null, text: content }
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return 'Session expired — reopen the panel from the Telegram menu button.'
    if (err.status === 429) return 'Too many messages are waiting — let GOAT finish first.'
    if (err.status === 503) return 'Chat is not available on the bot right now.'
    if (err.status === 422) return err.detail || 'That message cannot be sent.'
    if (err.status === 413 || err.status === 411) return 'That recording is too large to upload.'
  }
  return err instanceof Error ? err.message : String(err)
}

function Bubble({ role, children }: { role: 'user' | 'assistant'; children: React.ReactNode }) {
  const mine = role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm ${
          mine ? 'bg-accent/15 text-zinc-100' : 'border border-edge bg-surface2 text-zinc-200'
        }`}
      >
        {children}
      </div>
    </div>
  )
}

function ToolRow({
  name,
  args,
  end,
  stopped,
}: {
  name: string
  args: string
  end?: Extract<TurnEvent, { type: 'tool_end' }>
  stopped: boolean
}) {
  const state = end ? (end.ok ? 'ok' : 'failed') : stopped ? 'stopped' : 'running'
  const mark = { running: '…', ok: '✓', failed: '✗', stopped: '–' }[state]
  const color = { running: 'text-warn', ok: 'text-accent-green', failed: 'text-danger', stopped: 'text-zinc-500' }[state]
  return (
    <details className="rounded-lg border border-edge bg-surface px-2.5 py-1.5 text-xs text-zinc-400" data-testid="tool-row">
      <summary className="cursor-pointer select-none">
        <span className={color}>{mark}</span> <span className="font-medium text-zinc-300">{name}</span>
        {end && <span className="text-zinc-500"> · {end.chars} chars</span>}
        {!end && stopped && <span className="text-zinc-500"> · stopped</span>}
        {!end && !stopped && <span className="animate-pulse-live text-zinc-500"> · running</span>}
      </summary>
      <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words text-zinc-500">{args}</pre>
      {end && <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words text-zinc-300">{end.preview}</pre>}
    </details>
  )
}

function LiveTurn({ turn }: { turn: LocalTurn }) {
  const ends = new Map<string, Extract<TurnEvent, { type: 'tool_end' }>>()
  for (const e of turn.events) if (e.type === 'tool_end') ends.set(e.id, e)
  const waiting = turn.status === 'queued'
  const error = turn.events.find((e): e is Extract<TurnEvent, { type: 'error' }> => e.type === 'error')
  return (
    <div className="space-y-2" data-testid="live-turn">
      <Bubble role="user">{turn.text}</Bubble>
      {waiting && <p className="text-xs text-zinc-500">Waiting for the current turn to finish…</p>}
      {turn.events.map((e) =>
        e.type === 'tool_start' ? (
          <ToolRow key={e.seq} name={e.name} args={e.args} end={ends.get(e.id)} stopped={isTerminal(turn.status)} />
        ) : null,
      )}
      {turn.status === 'running' && turn.events.length <= 1 && <p className="animate-pulse-live text-xs text-zinc-500">GOAT is thinking…</p>}
      {turn.reply && <Bubble role="assistant">{turn.reply}</Bubble>}
      {error && <Banner kind="error" message={error.message} />}
      {turn.status === 'cancelled' && <p className="text-xs text-zinc-500">Stopped.</p>}
    </div>
  )
}

type LocalTurn = {
  id: string
  text: string
  status: TurnStatus
  reply: string | null
  events: TurnEvent[]
  doneAt?: number
  /** How many stored user messages already had this text when it was sent (so an older identical one is not hidden). */
  baseline?: number
}

const OVERLAY = 'absolute inset-0 flex w-full items-center gap-2.5 rounded-xl border bg-surface px-3 text-left text-sm'
const CLAMP2 = '[display:-webkit-box] [-webkit-line-clamp:2] [-webkit-box-orient:vertical] overflow-hidden break-words'

/** The state of a voice conversation, laid over the message box (same size, so nothing moves). */
function ConversationOverlay({
  phase, muted, interim, onInterrupt,
}: { phase: Phase; muted: boolean; interim: string; onInterrupt: () => void }) {
  if (phase === 'speaking') {
    return (
      <button
        type="button"
        aria-label="Interrupt GOAT"
        data-testid="conversation-overlay"
        onMouseDown={keepFocus}
        onClick={onInterrupt}
        className={`${OVERLAY} border-accent/50 text-zinc-100`}
      >
        <SpeakerIcon className="h-4 w-4 shrink-0 animate-pulse-live text-accent" />
        <span className="min-w-0 truncate">Speaking — tap to interrupt</span>
      </button>
    )
  }
  let body: React.ReactNode
  if (phase === 'connecting' || phase === 'thinking') {
    body = (
      <>
        <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-zinc-500 border-t-transparent" />
        <span>{phase === 'connecting' ? 'Connecting…' : 'GOAT is thinking…'}</span>
      </>
    )
  } else if (muted) {
    body = (
      <>
        <MicOffIcon className="h-4 w-4 shrink-0 text-warn" />
        <span className="text-zinc-400">Muted</span>
      </>
    )
  } else {
    body = (
      <>
        <span className="h-2.5 w-2.5 shrink-0 animate-pulse-live rounded-full bg-accent-green" />
        <span className={`min-w-0 ${interim ? `${CLAMP2} text-zinc-100` : 'truncate text-zinc-400'}`}>{interim || 'Listening…'}</span>
      </>
    )
  }
  return (
    <div role="status" aria-live="polite" data-testid="conversation-overlay" className={`${OVERLAY} border-edge`}>
      {body}
    </div>
  )
}

export function Chat() {
  const [text, setText] = useState('')
  const [sendError, setSendError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [turns, setTurns] = useState<LocalTurn[]>([])
  const cursors = useRef<Record<string, number>>({})
  const inFlight = useRef(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const stick = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  useFillViewportHeight(rootRef)
  useAutosize(textareaRef, text)

  const state = usePolling<ChatState>(() => apiGet('/api/chat/state'), 2000, [])
  const chatId = state.data?.chat_id ?? null
  const hasActive = turns.some((t) => !isTerminal(t.status))
  const history = usePolling<HistoryResponse | null>(
    () => (chatId ? apiGet('/api/chat/history') : Promise.resolve(null)),
    hasActive ? 2000 : 4000,
    [chatId],
  )

  // A turn that was already running when the page opened (reload, other device): pick it up.
  const adoptedRef = useRef(false)
  useEffect(() => {
    const id = state.data?.active_turn_id
    if (!id || adoptedRef.current || turns.some((t) => t.id === id)) return
    adoptedRef.current = true
    setTurns((prev) => [...prev, { id, text: '', status: 'running', reply: null, events: [] }])
  }, [state.data?.active_turn_id, turns])

  const activeKey = turns.filter((t) => !isTerminal(t.status)).map((t) => t.id).join(',')
  useEffect(() => {
    if (!activeKey) return
    let stopped = false
    const tick = async () => {
      if (inFlight.current) return
      inFlight.current = true
      try {
        for (const id of activeKey.split(',')) {
          const view = await apiGet<TurnView>(`/api/chat/turns/${id}?after=${cursors.current[id] ?? 0}`)
          if (stopped) return
          const fresh = view.events.filter((e) => e.seq > (cursors.current[id] ?? 0))
          if (fresh.length) cursors.current[id] = fresh[fresh.length - 1].seq
          setTurns((prev) =>
            prev.map((t) =>
              t.id === id
                ? {
                    ...t,
                    text: t.text || view.text,
                    status: view.status,
                    reply: view.reply,
                    events: [...t.events, ...fresh],
                    doneAt: isTerminal(view.status) ? (t.doneAt ?? Date.now()) : undefined,
                  }
                : t,
            ),
          )
          if (isTerminal(view.status)) history.refetch()
        }
      } catch {
        /* a failed poll is retried on the next tick */
      } finally {
        inFlight.current = false
      }
    }
    void tick()
    const timer = setInterval(() => void tick(), POLL_TURN_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey])

  const transcript = useMemo(
    () =>
      (history.data?.messages ?? []).filter((e) => e.role === 'user' || e.role === 'assistant'),
    [history.data],
  )

  // The stored conversation can already hold the user's message while its turn is still running: show it once.
  const hiddenUserEntries = useMemo(() => {
    const hidden = new Set<number>()
    for (const t of turns) {
      if (!t.text || t.baseline === undefined || t.status === 'done') continue
      const same = transcript.flatMap((e, i) => (e.role === 'user' && e.content === t.text ? [i] : []))
      if (same.length > t.baseline) hidden.add(same[same.length - 1])
    }
    return hidden
  }, [turns, transcript])

  // Once a finished turn shows up in the stored conversation, drop its live copy (no duplicate bubbles).
  const statusKey = turns.map((t) => `${t.id}:${t.status}`).join(',')
  useEffect(() => {
    setTurns((prev) => {
      const next = prev.filter((t) => {
        if (t.status !== 'done' || !t.reply) return true
        const probe = t.reply.slice(0, 80)
        const stored = transcript.some((e) => e.role === 'assistant' && e.content.includes(probe))
        const stale = t.doneAt !== undefined && Date.now() - t.doneAt > DONE_FALLBACK_MS
        return !(stored || stale)
      })
      return next.length === prev.length ? prev : next
    })
  }, [transcript, statusKey])

  // Follow the conversation only while the user is already at the bottom; if they scrolled up to read, leave them there.
  const toBottom = useCallback(() => {
    const el = scrollerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])
  const onScroll = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    stick.current = near
    setAtBottom((prev) => (prev === near ? prev : near))
  }, [])
  const contentKey = `${transcript.length}|${hiddenUserEntries.size}|${turns
    .map((t) => `${t.id}:${t.status}:${t.events.length}:${t.reply ? 1 : 0}`)
    .join(',')}`
  useLayoutEffect(() => {
    if (stick.current) toBottom()
  }, [contentKey, toBottom])
  // The list can change height without new data (a tool row opened, a live turn swapped for its stored copy), and its
  // visible part shrinks when the message box grows or the phone keyboard opens: stay pinned in all of those cases.
  useEffect(() => {
    const content = contentRef.current
    const scroller = scrollerRef.current
    if (!content || !scroller || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (stick.current) toBottom()
    })
    observer.observe(content)
    observer.observe(scroller)
    return () => observer.disconnect()
  }, [toBottom])
  const jumpToLatest = useCallback(() => {
    stick.current = true
    setAtBottom(true)
    toBottom()
  }, [toBottom])

  /** Send one message to GOAT (typed, or said in a voice conversation). Returns the id of the turn it started. */
  const sendText = useCallback(
    async (message: string): Promise<string | null> => {
      setSendError(null)
      setNotice(null)
      stick.current = true
      setAtBottom(true)
      try {
        const { turn_id } = await apiPost<{ turn_id: string }>('/api/chat', { text: message })
        const baseline = transcript.filter((e) => e.role === 'user' && e.content === message).length
        setTurns((prev) => [...prev, { id: turn_id, text: message, status: 'queued', reply: null, events: [], baseline }])
        return turn_id
      } catch (err) {
        setSendError(describeError(err))
        return null
      }
    },
    [transcript],
  )

  const send = useCallback(async () => {
    const message = text.trim()
    if (!message) return
    if (await sendText(message)) {
      setText('')
      textareaRef.current?.focus()
    }
  }, [text, sendText])

  // PROTOTYPE: the server answers a stub (text is empty) until a speech-to-text provider is connected.
  const sendVoice = useCallback(async (clip: VoiceClip) => {
    setSendError(null)
    setNotice(null)
    const form = new FormData()
    form.append('audio', clip.blob, `voice.${audioExtension(clip.mimeType)}`)
    form.append('duration_ms', String(Math.round(clip.durationMs)))
    try {
      const res = await apiPostForm<VoiceResponse>('/api/voice', form)
      if (res.text) {
        setText((prev) => (prev.trim() ? `${prev.trim()} ${res.text}` : res.text))
        textareaRef.current?.focus()
      } else {
        const limit = clip.limitReached ? ` Stopped at the ${VOICE_MAX_MS / 1000} s limit.` : ''
        setNotice(
          `Voice message received (${(clip.durationMs / 1000).toFixed(1)} s, ${Math.max(1, Math.round(res.bytes / 1024))} KB). ` +
            `Transcription is not connected yet (prototype).${limit}`,
        )
      }
    } catch (err) {
      setSendError(describeError(err))
    }
  }, [])
  const voice = useVoiceRecorder(sendVoice)
  const recording = voice.state === 'recording'
  const conversation = useConversation({ turns, submit: sendText })
  const inConversation = conversation.active
  const problem = sendError ?? voice.error ?? conversation.error
  const shownNotice = notice ?? conversation.notice

  const stop = useCallback(async () => {
    const target = turns.find((t) => t.status === 'running') ?? turns.find((t) => t.status === 'queued')
    if (!target) return
    try {
      await apiPost(`/api/chat/turns/${target.id}/cancel`)
    } catch (err) {
      setSendError(describeError(err))
    }
  }, [turns])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send()
    } else if (e.key === 'Escape' && inConversation) {
      e.preventDefault()
      conversation.end()
    } else if (e.key === 'Escape' && hasActive) {
      e.preventDefault()
      void stop()
    }
  }

  const external = !!state.data?.external_busy
  const working = hasActive || !!state.data?.busy
  const status = external ? 'GOAT is answering on Telegram — your message will wait its turn' : working ? 'GOAT is working…' : 'Ready'

  const seen = new Map<string, number>()
  return (
    <div
      ref={rootRef}
      className="flex flex-col gap-3"
      style={{ height: 'calc(var(--chat-h, 70vh) - max(0px, env(safe-area-inset-bottom, 0px) - 1rem))' }}
    >
      {state.error && <Banner kind="error" message={state.error} />}
      {history.data?.warnings?.map((w) => <Banner key={w} kind="warning" message={w} />)}
      <div className="flex min-h-[1.25rem] shrink-0 items-center gap-2 text-xs text-zinc-400" aria-live="polite">
        <span className={`h-2 w-2 shrink-0 rounded-full ${working ? 'animate-pulse-live bg-warn' : 'bg-accent-green'}`} />
        <span className="min-w-0 truncate">{status}</span>
        {chatId && (
          <span className="hidden shrink-0 text-zinc-600 sm:inline">· one conversation with Telegram (chat {chatId})</span>
        )}
        <button
          type="button"
          title="Who reads GOAT's replies aloud in a conversation: Deepgram's Romanian voice, or this device's own voice"
          onMouseDown={keepFocus}
          onClick={() => conversation.setVoiceKind(conversation.voiceKind === 'deepgram' ? 'browser' : 'deepgram')}
          className="ml-auto h-7 shrink-0 rounded-full px-2 text-[11px] text-zinc-500 active:bg-surface2"
        >
          {conversation.voiceKind === 'deepgram' ? 'Voice: Deepgram' : 'Voice: browser'}
        </button>
        <button
          type="button"
          aria-label={inConversation ? 'End voice conversation' : 'Start voice conversation'}
          aria-pressed={inConversation}
          title={inConversation ? 'End the voice conversation' : 'Talk with GOAT: it listens, answers and speaks'}
          disabled={!chatId || voice.state !== 'idle'}
          onMouseDown={keepFocus}
          onClick={inConversation ? conversation.end : conversation.start}
          className={`flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[11px] transition-colors disabled:opacity-40 ${
            inConversation ? 'border-accent bg-accent/15 text-accent' : 'border-edge text-zinc-400 active:bg-surface2'
          }`}
        >
          <HeadsetIcon className="h-3.5 w-3.5" />
          {inConversation ? `On · ${clock(conversation.elapsedMs)}` : 'Conversation'}
        </button>
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollerRef}
          onScroll={onScroll}
          className="h-full overflow-y-auto overscroll-contain rounded-xl border border-edge bg-surface p-3"
          data-testid="chat-transcript"
        >
          <div ref={contentRef} className="space-y-3">
            {!chatId && !state.error && <p className="py-10 text-center text-zinc-400">Loading…</p>}
            {transcript.map((e, i) => {
              if (hiddenUserEntries.has(i)) return null
              const { tools, text: body } =
                e.role === 'assistant' ? splitToolSummary(e.content) : { tools: null, text: e.content }
              const base = `${e.timestamp}-${e.role}`
              const n = seen.get(base) ?? 0
              seen.set(base, n + 1)
              return (
                <div key={`${base}-${n}`} className="space-y-1.5">
                  {tools && (
                    <details className="rounded-lg border border-edge px-2.5 py-1.5 text-xs text-zinc-500">
                      <summary className="cursor-pointer select-none">tools used</summary>
                      <pre className="mt-1.5 whitespace-pre-wrap break-words">{tools}</pre>
                    </details>
                  )}
                  <Bubble role={e.role as 'user' | 'assistant'}>{body}</Bubble>
                </div>
              )
            })}
            {turns.map((t) => (
              <LiveTurn key={t.id} turn={t} />
            ))}
          </div>
        </div>
        {!atBottom && (
          <button
            type="button"
            onMouseDown={keepFocus}
            onClick={jumpToLatest}
            className="absolute bottom-3 right-4 rounded-full border border-edge bg-surface2 px-3 py-1.5 text-xs text-zinc-200 shadow-card"
          >
            ↓ Latest
          </button>
        )}
      </div>

      <div className="relative shrink-0">
        {(problem || shownNotice) && (
          <div className="absolute bottom-full left-0 right-0 z-10 mb-2 overflow-hidden rounded-lg bg-base shadow-card [&>div]:mb-0">
            {problem && <Banner kind="error" message={problem} />}
            {shownNotice && !problem && <Banner kind="info" message={shownNotice} />}
          </div>
        )}
        <div className="flex items-end gap-2">
          <div className="relative min-w-0 flex-1">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              readOnly={voice.state !== 'idle' || inConversation}
              rows={2}
              placeholder="Message GOAT — Enter sends, Shift+Enter new line"
              aria-label="Message GOAT"
              className="min-h-[44px] w-full resize-none overflow-y-hidden rounded-xl border border-edge bg-surface px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none"
            />
            {inConversation && (
              <ConversationOverlay
                phase={conversation.phase}
                muted={conversation.muted}
                interim={conversation.interim}
                onInterrupt={conversation.interrupt}
              />
            )}
            {!inConversation && voice.state !== 'idle' && (
              <div
                role="status"
                aria-live="polite"
                data-testid="voice-overlay"
                className={`absolute inset-0 flex items-center gap-2.5 rounded-xl border bg-surface px-3 text-sm ${
                  recording ? 'border-danger/50 text-zinc-100' : 'border-edge text-zinc-400'
                }`}
              >
                {recording ? (
                  <>
                    <span className="h-2.5 w-2.5 shrink-0 animate-pulse-live rounded-full bg-danger shadow-[0_0_10px_rgba(248,113,113,0.8)]" />
                    <span className="tabular-nums">
                      Recording {clock(voice.elapsedMs)} / {clock(VOICE_MAX_MS)}
                    </span>
                    <span className="hidden min-w-0 truncate text-xs text-zinc-500 sm:inline">tap the mic to stop</span>
                  </>
                ) : (
                  <>
                    <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-zinc-500 border-t-transparent" />
                    <span>Sending audio…</span>
                  </>
                )}
              </div>
            )}
          </div>
          {inConversation ? (
            <button
              type="button"
              aria-label={conversation.muted ? 'Unmute microphone' : 'Mute microphone'}
              aria-pressed={conversation.muted}
              onMouseDown={keepFocus}
              onClick={conversation.toggleMute}
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors ${
                conversation.muted ? 'border-warn/50 text-warn' : 'border-edge text-zinc-300 active:bg-surface2'
              }`}
            >
              {conversation.muted ? <MicOffIcon className="h-5 w-5" /> : <MicIcon className="h-5 w-5" />}
            </button>
          ) : (
            <button
              type="button"
              aria-label={recording ? 'Stop recording' : 'Record voice message'}
              title={recording ? 'Stop recording' : 'Record a voice message'}
              disabled={voice.state === 'sending'}
              onMouseDown={keepFocus}
              onClick={voice.toggle}
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors disabled:opacity-40 ${
                recording ? 'animate-pulse-live border-danger bg-danger text-base' : 'border-edge text-zinc-300 active:bg-surface2'
              }`}
            >
              <MicIcon className="h-5 w-5" />
            </button>
          )}
          {/* One slot: End during a voice conversation; else Stop while a response is in progress; else Send (Enter still queues). */}
          {inConversation ? (
            <button
              type="button"
              aria-label="End conversation"
              title="End the voice conversation (Esc)"
              onMouseDown={keepFocus}
              onClick={conversation.end}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-danger text-base active:opacity-80"
            >
              <EndCallIcon className="h-5 w-5" />
            </button>
          ) : hasActive ? (
            <button
              type="button"
              aria-label="Stop"
              title="Stop (Esc)"
              onMouseDown={keepFocus}
              onClick={() => void stop()}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-danger/40 text-danger active:bg-surface2"
            >
              <StopGlyph />
            </button>
          ) : (
            <button
              type="button"
              aria-label="Send"
              title="Send (Enter)"
              disabled={!text.trim() || !chatId}
              onMouseDown={keepFocus}
              onClick={() => void send()}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent text-base transition-opacity disabled:opacity-40"
            >
              <SendGlyph />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
