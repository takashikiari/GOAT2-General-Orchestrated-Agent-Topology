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

export function Dashboard() {
  const { data, error, loading } = usePolling<MetricsReport>(() => apiGet('/api/metrics'), 15000, [])

  if (error) return <Banner kind="error" message={error} />
  if (loading && !data) return <p className="py-10 text-center text-zinc-600">Loading dashboard…</p>
  if (!data) return null

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
