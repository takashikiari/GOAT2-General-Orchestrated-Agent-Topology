import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { ApiError, apiGet, apiPost } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type ChatState = { chat_id: string | null; busy: boolean; external_busy: boolean; active_turn_id: string | null }

type TimelineEntry = { tier: 'L2' | 'L3'; timestamp: number; role: string; content: string }
type ConversationResponse = { chat_id: string; timeline: TimelineEntry[]; warnings?: string[] }

type TurnStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'
type TurnEvent =
  | { seq: number; ts: number; type: 'queued' | 'started' | 'cancelled' }
  | { seq: number; ts: number; type: 'tool_start'; id: string; name: string; args: string }
  | { seq: number; ts: number; type: 'tool_end'; id: string; name: string; chars: number; ok: boolean; preview: string }
  | { seq: number; ts: number; type: 'reply'; text: string }
  | { seq: number; ts: number; type: 'error'; message: string }
type TurnView = { id: string; text: string; status: TurnStatus; reply: string | null; events: TurnEvent[] }

const POLL_TURN_MS = 700
const DONE_FALLBACK_MS = 30000
const isTerminal = (s: TurnStatus) => s === 'done' || s === 'error' || s === 'cancelled'

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

export function Chat() {
  const [text, setText] = useState('')
  const [sendError, setSendError] = useState<string | null>(null)
  const [turns, setTurns] = useState<LocalTurn[]>([])
  const cursors = useRef<Record<string, number>>({})
  const inFlight = useRef(false)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  const state = usePolling<ChatState>(() => apiGet('/api/chat/state'), 2000, [])
  const chatId = state.data?.chat_id ?? null
  const hasActive = turns.some((t) => !isTerminal(t.status))
  const history = usePolling<ConversationResponse | null>(
    () => (chatId ? apiGet(`/api/conversations/${encodeURIComponent(chatId)}`) : Promise.resolve(null)),
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
      (history.data?.timeline ?? []).filter((e) => e.tier === 'L2' && (e.role === 'user' || e.role === 'assistant')),
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [transcript.length, turns])

  const send = useCallback(async () => {
    const message = text.trim()
    if (!message) return
    setSendError(null)
    try {
      const { turn_id } = await apiPost<{ turn_id: string }>('/api/chat', { text: message })
      const baseline = transcript.filter((e) => e.role === 'user' && e.content === message).length
      setTurns((prev) => [...prev, { id: turn_id, text: message, status: 'queued', reply: null, events: [], baseline }])
      setText('')
    } catch (err) {
      setSendError(describeError(err))
    }
  }, [text, transcript])

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
    } else if (e.key === 'Escape' && hasActive) {
      e.preventDefault()
      void stop()
    }
  }

  const external = !!state.data?.external_busy
  const working = hasActive || !!state.data?.busy
  const status = external ? 'GOAT is answering on Telegram — your message will wait its turn' : working ? 'GOAT is working…' : 'Ready'

  return (
    <div className="flex min-h-[70vh] flex-col gap-3">
      {state.error && <Banner kind="error" message={state.error} />}
      {history.data?.warnings?.map((w) => <Banner key={w} kind="warning" message={w} />)}
      <div className="flex items-center gap-2 text-xs text-zinc-400" aria-live="polite">
        <span className={`h-2 w-2 rounded-full ${working ? 'animate-pulse-live bg-warn' : 'bg-accent-green'}`} />
        <span>{status}</span>
        {chatId && <span className="text-zinc-600">· one conversation with Telegram (chat {chatId})</span>}
      </div>

      <div className="flex-1 space-y-3 rounded-xl border border-edge bg-surface p-3" data-testid="chat-transcript">
        {!chatId && !state.error && <p className="py-10 text-center text-zinc-400">Loading…</p>}
        {transcript.map((e, i) => {
          if (hiddenUserEntries.has(i)) return null
          const { tools, text: body } = e.role === 'assistant' ? splitToolSummary(e.content) : { tools: null, text: e.content }
          return (
            <div key={`${e.timestamp}-${i}`} className="space-y-1.5">
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
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 space-y-2 bg-base pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-1">
        {sendError && <Banner kind="error" message={sendError} />}
        <div className="flex items-end gap-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Message GOAT — Enter to send, Shift+Enter for a new line, Esc to stop"
            aria-label="Message GOAT"
            className="min-h-[44px] flex-1 resize-none rounded-xl border border-edge bg-surface px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none"
          />
          {hasActive && (
            <button
              onClick={() => void stop()}
              className="min-h-[44px] rounded-xl border border-danger/40 px-4 text-sm text-danger active:bg-surface2"
            >
              Stop
            </button>
          )}
          <button
            onClick={() => void send()}
            disabled={!text.trim() || !chatId}
            className="min-h-[44px] rounded-xl bg-accent px-4 text-sm font-medium text-base disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
