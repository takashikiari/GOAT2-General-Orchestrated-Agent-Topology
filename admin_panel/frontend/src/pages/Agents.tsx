import type { ReactNode } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type AgentMessage = { from: string; to: string; text: string; alert: boolean; origin_warning: string | null }
type AgentTurn = {
  timestamp: number
  reply_timestamp: number | null
  messages: AgentMessage[]
  goat_reply: string | null
  tools: string | null
}
type AgentsResponse = { chat_id: string; turns: AgentTurn[]; warnings?: string[] }

const LONG_TEXT = 320

function when(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const today = new Date()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === today.toDateString() ? time : `${d.toLocaleDateString()} ${time}`
}

function Chip({ tone, children }: { tone: 'warn' | 'danger'; children: ReactNode }) {
  const color = tone === 'warn' ? 'border-warn/30 bg-warn/10 text-warn' : 'border-danger/30 bg-danger/10 text-danger'
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] ${color}`}>{children}</span>
}

/** A long message is folded to its first lines; the full text opens in place. */
function LongText({ text }: { text: string }) {
  if (text.length <= LONG_TEXT) return <p className="whitespace-pre-wrap break-words text-sm text-zinc-200">{text}</p>
  return (
    <details className="text-sm text-zinc-200">
      <summary className="cursor-pointer select-none whitespace-pre-wrap break-words">{text.slice(0, LONG_TEXT)}…</summary>
      <p className="mt-1.5 whitespace-pre-wrap break-words">{text}</p>
    </details>
  )
}

function TurnCard({ turn }: { turn: AgentTurn }) {
  return (
    <article className="animate-fade-in space-y-3 rounded-xl border border-edge bg-surface p-4" data-testid="agent-turn">
      <p className="text-xs text-zinc-500">{when(turn.timestamp)}</p>
      {turn.messages.length === 0 && <p className="text-sm text-zinc-500">(the messages of this batch could not be read)</p>}
      {turn.messages.map((m, i) => (
        <div key={i} className="space-y-1" data-testid="agent-message">
          <p className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="break-all font-medium text-accent">{m.from}</span>
            <span className="text-zinc-600">→</span>
            <span className="break-all text-zinc-400">{m.to}</span>
            {m.alert && <Chip tone="warn">supervisor alert</Chip>}
            {m.origin_warning && <Chip tone="danger">real origin: {m.origin_warning}</Chip>}
          </p>
          <LongText text={m.text} />
        </div>
      ))}
      {turn.goat_reply && (
        <details className="rounded-lg border border-edge bg-surface2 px-3 py-2 text-sm" open>
          <summary className="cursor-pointer select-none text-xs text-zinc-400">
            GOAT's reply {turn.reply_timestamp ? `· ${when(turn.reply_timestamp)}` : ''}
          </summary>
          <p className="mt-1.5 whitespace-pre-wrap break-words text-zinc-200">{turn.goat_reply}</p>
          {turn.tools && (
            <details className="mt-1.5 text-xs text-zinc-500">
              <summary className="cursor-pointer select-none">tools used</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words">{turn.tools}</pre>
            </details>
          )}
        </details>
      )}
    </article>
  )
}

/** What other agent-coord sessions told GOAT, and what GOAT answered: kept out of the chat on purpose. */
export function Agents() {
  const { data, error, loading } = usePolling<AgentsResponse>(() => apiGet('/api/agents'), 5000, [])
  const turns = [...(data?.turns ?? [])].reverse() // newest first

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Messages other agent-coord sessions sent to GOAT, with GOAT&apos;s reply. They do not appear in the Chat.
      </p>
      {error && <Banner kind="error" message={error} />}
      {data?.warnings?.map((w) => <Banner key={w} kind="warning" message={w} />)}
      {loading && !data && <p className="py-10 text-center text-zinc-400">Loading…</p>}
      {data && turns.length === 0 && (
        <p className="py-10 text-center text-zinc-400">No agent-coord messages in GOAT&apos;s recent conversation yet.</p>
      )}
      {turns.map((t) => (
        <TurnCard key={`${t.timestamp}`} turn={t} />
      ))}
    </div>
  )
}
