import { useState } from 'react'
import type { ReactNode } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type MetricsReport = {
  cache_hit_rate: number
  prefetch_success_rate: number
  prefetch_timeout_rate: number
  tier_hit_rates: Record<string, number>
  top_intents: Record<string, number>
  avg_tokens_injected: number
  avg_latency_total: number
  avg_llm_calls: number
  activation_cold_rate: number
  activation_warm_rate: number
  activation_drift_rate: number
  warm_served_rate: number
  thread_breaks: number
  enriching_writes: number
  filing_writes: number
}

function pct(n: number | undefined): string {
  return n === undefined ? '—' : `${(n * 100).toFixed(1)}%`
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="animate-fade-in rounded-xl border border-edge bg-gradient-to-br from-surface to-surface2 p-4 shadow-card transition-shadow hover:shadow-glow">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</h3>
      <div className="space-y-1.5 text-sm text-zinc-300">{children}</div>
    </div>
  )
}

type TunnelInfo = {
  status: 'off' | 'starting' | 'ready' | 'down' | 'unavailable'
  url: string | null
  since: number | null
  detail: string
}

const TUNNEL_LABEL: Record<TunnelInfo['status'], { text: string; dot: string }> = {
  ready: { text: 'Online', dot: 'bg-accent-green' },
  starting: { text: 'Starting…', dot: 'animate-pulse-live bg-warn' },
  down: { text: 'Down', dot: 'bg-danger' },
  unavailable: { text: 'Unavailable', dot: 'bg-danger' },
  off: { text: 'Off', dot: 'bg-zinc-600' },
}

/** The Cloudflare quick-tunnel link the panel is reachable on right now (a new one is created on every bot start). */
function TunnelCard() {
  const { data } = usePolling<TunnelInfo>(() => apiGet('/api/tunnel'), 5000, [])
  const [copied, setCopied] = useState(false)
  if (!data || typeof data.status !== 'string' || !(data.status in TUNNEL_LABEL)) return null
  const label = TUNNEL_LABEL[data.status]
  const canCopy = !!data.url && typeof navigator !== 'undefined' && !!navigator.clipboard
  const copy = async () => {
    if (!data.url) return
    try {
      await navigator.clipboard.writeText(data.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked: the link stays selectable */
    }
  }
  return (
    <Card title="Panel link (Cloudflare tunnel)">
      <p className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${label.dot}`} />
        <span>{label.text}</span>
        {data.since && <span className="text-xs text-zinc-500">since {new Date(data.since * 1000).toLocaleTimeString()}</span>}
      </p>
      {data.url && (
        <p className="flex flex-wrap items-center gap-2">
          <a href={data.url} target="_blank" rel="noreferrer" className="break-all text-accent underline">
            {data.url}
          </a>
          {canCopy && (
            <button
              type="button"
              onClick={() => void copy()}
              className="min-h-[32px] rounded-lg border border-edge px-2.5 text-xs text-zinc-300 active:bg-surface2"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
        </p>
      )}
      {!data.url && data.detail && <p className="text-xs text-zinc-500">{data.detail}</p>}
      <p className="text-xs text-zinc-600">A new link is created every time the bot starts; this always shows the current one.</p>
    </Card>
  )
}

type LiveUsage = {
  provider: string | null
  today_minutes: number
  limit_minutes: number
  sessions_today: number
  limit_reached: boolean
}

/** The live voice's minutes today against its daily cap (it is billed by the minute); hidden when there is no live voice. */
function LiveVoiceCard() {
  const { data } = usePolling<LiveUsage>(() => apiGet('/api/voice/live/usage'), 30000, [])
  if (!data || !data.provider || typeof data.today_minutes !== 'number' || typeof data.limit_minutes !== 'number') return null
  const share = data.limit_minutes > 0 ? Math.min(1, data.today_minutes / data.limit_minutes) : 1
  return (
    <Card title={`Live voice minutes (${data.provider})`}>
      <p>
        Today: {data.today_minutes.toFixed(1)} / {data.limit_minutes} min · {data.sessions_today}{' '}
        {data.sessions_today === 1 ? 'conversation' : 'conversations'}
      </p>
      <div className="h-2 overflow-hidden rounded-full bg-surface2" role="progressbar" aria-valuenow={Math.round(share * 100)}>
        <div className={`h-full ${data.limit_reached ? 'bg-danger' : share > 0.8 ? 'bg-warn' : 'bg-accent-green'}`} style={{ width: `${share * 100}%` }} />
      </div>
      {data.limit_reached ? (
        <p className="text-xs text-danger">Daily limit reached: until tomorrow, voice conversations use the Deepgram voice.</p>
      ) : (
        <p className="text-xs text-zinc-600">The cap is VOICE_LIVE_DAILY_MINUTES in GOAT's .env.</p>
      )}
    </Card>
  )
}

export function Dashboard() {
  const { data, error, loading } = usePolling<MetricsReport>(() => apiGet('/api/metrics'), 15000, [])

  return (
    <div className="space-y-4">
      <TunnelCard />
      <LiveVoiceCard />
      {error ? (
        <Banner kind="error" message={error} />
      ) : loading && !data ? (
        <p className="py-10 text-center text-zinc-400">Loading dashboard…</p>
      ) : data ? (
        <MetricsGrid data={data} />
      ) : null}
    </div>
  )
}

function MetricsGrid({ data }: { data: MetricsReport }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Card title="Cache & Prefetch">
        <p>Hit rate: {pct(data.cache_hit_rate)}</p>
        <p>Prefetch success: {pct(data.prefetch_success_rate)}</p>
        <p>Prefetch timeout: {pct(data.prefetch_timeout_rate)}</p>
      </Card>
      <Card title="Tier Hit Rates">
        {Object.entries(data.tier_hit_rates ?? {}).map(([tier, rate]) => (
          <p key={tier}>
            {tier}: {pct(rate)}
          </p>
        ))}
      </Card>
      <Card title="Top Intents">
        {Object.entries(data.top_intents ?? {}).map(([intent, count]) => (
          <p key={intent}>
            {intent}: {count}
          </p>
        ))}
      </Card>
      <Card title="Latency & Tokens">
        <p>Avg total latency: {data.avg_latency_total?.toFixed(3)}s</p>
        <p>Avg tokens injected: {data.avg_tokens_injected?.toFixed(0)}</p>
        <p>Avg LLM calls: {data.avg_llm_calls?.toFixed(2)}</p>
      </Card>
      <Card title="Activation State">
        <p>Cold: {pct(data.activation_cold_rate)}</p>
        <p>Warm: {pct(data.activation_warm_rate)}</p>
        <p>Drift: {pct(data.activation_drift_rate)}</p>
        <p>Warm served: {pct(data.warm_served_rate)}</p>
      </Card>
      <Card title="Writes">
        <p>Enriching: {data.enriching_writes}</p>
        <p>Filing: {data.filing_writes}</p>
        <p>Thread breaks: {data.thread_breaks}</p>
      </Card>
    </div>
  )
}
