import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { useIsMobile } from '../hooks/useIsMobile'
import { usePolling } from '../hooks/usePolling'

type LogsResponse = { lines?: string[]; error?: string }
type MetricsSlice = {
  total_requests?: number
  avg_latency_total?: number
  tier_hit_rates?: Record<string, number>
  error?: string
}

type Level = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'
const LEVELS: { id: Level; label: string }[] = [
  { id: 'ALL', label: 'All' },
  { id: 'INFO', label: 'Info' },
  { id: 'WARNING', label: 'Warn' },
  { id: 'ERROR', label: 'Error' },
]

type ParsedLine = { raw: string; timestamp: Date | null; level: string; message: string }

const LOG_LINE_RE = /^(\S+)\s+(\S+)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$/
const SCROLL_BOTTOM_THRESHOLD_PX = 32

export function parseLogLine(raw: string): ParsedLine {
  const match = LOG_LINE_RE.exec(raw)
  if (!match) return { raw, timestamp: null, level: '', message: raw }
  const [, ts, , level, message] = match
  const parsedTs = new Date(ts)
  return { raw, timestamp: Number.isNaN(parsedTs.getTime()) ? null : parsedTs, level, message }
}

function levelColor(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'text-danger'
  if (level === 'WARNING') return 'text-warn'
  if (level === 'DEBUG') return 'text-zinc-500'
  return 'text-zinc-300'
}

function LiveBadge({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-edge bg-surface px-3 py-1.5 text-xs">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-pulse-live rounded-full bg-accent-green" />
      </span>
      <span className="font-semibold tracking-wide text-accent-green">LIVE</span>
      <span className="text-zinc-500">· {count} in last min</span>
    </div>
  )
}

function MetricsMini({ metrics }: { metrics: MetricsSlice }) {
  if (metrics.error) return <Banner kind="error" message={metrics.error} />
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-edge bg-surface p-3">
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Processed</div>
          <div className="mt-1 text-xl font-semibold text-accent">{metrics.total_requests ?? '—'}</div>
        </div>
        <div className="rounded-lg border border-edge bg-surface p-3">
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Avg latency</div>
          <div className="mt-1 text-xl font-semibold text-accent">
            {metrics.avg_latency_total !== undefined ? `${metrics.avg_latency_total.toFixed(2)}s` : '—'}
          </div>
        </div>
      </div>
      <div className="rounded-lg border border-edge bg-surface p-3">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-zinc-500">Memory tier hit rate</div>
        <div className="space-y-1.5">
          {Object.entries(metrics.tier_hit_rates ?? {}).map(([tier, rate]) => (
            <div key={tier} className="flex items-center gap-2 text-xs">
              <span className="w-16 shrink-0 truncate text-zinc-400" title={tier}>
                {tier}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface2">
                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(rate * 100, 100)}%` }} />
              </div>
              <span className="w-10 text-right text-zinc-400">{(rate * 100).toFixed(0)}%</span>
            </div>
          ))}
          {Object.keys(metrics.tier_hit_rates ?? {}).length === 0 && (
            <p className="text-xs text-zinc-600">No tier activity yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export function Live() {
  const [level, setLevel] = useState<Level>('ALL')
  const [paused, setPaused] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const isMobile = useIsMobile()

  const logs = usePolling<LogsResponse>(
    () => apiGet(`/api/logs?minutes=5&level=${level}&limit=200`),
    2500,
    [level],
  )
  const metrics = usePolling<MetricsSlice>(() => apiGet('/api/metrics'), 3000, [])

  const parsed = useMemo(() => (logs.data?.lines ?? []).map(parseLogLine), [logs.data])

  const eventsLastMinute = useMemo(() => {
    const cutoff = Date.now() - 60_000
    return parsed.filter((p) => p.timestamp !== null && p.timestamp.getTime() >= cutoff).length
  }, [parsed])

  useEffect(() => {
    if (paused) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [parsed, paused])

  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distanceFromBottom > SCROLL_BOTTOM_THRESHOLD_PX) setPaused(true)
  }

  function resume() {
    setPaused(false)
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  const feed = (
    <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-edge bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge p-3">
        <LiveBadge count={eventsLastMinute} />
        <div className="flex gap-1">
          {LEVELS.map((l) => (
            <button
              key={l.id}
              onClick={() => setLevel(l.id)}
              className={`min-h-[36px] rounded-full px-3 text-xs font-medium transition-colors ${
                level === l.id ? 'bg-accent/20 text-accent' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>

      {logs.error && <div className="p-3"><Banner kind="error" message={logs.error} /></div>}
      {logs.data?.error && <div className="p-3"><Banner kind="error" message={logs.data.error} /></div>}

      <div ref={scrollRef} onScroll={onScroll} className="relative min-h-[50vh] flex-1 overflow-y-auto p-3 font-mono text-xs">
        {parsed.length === 0 && !logs.error && (
          <p className="py-10 text-center text-zinc-600">Waiting for activity…</p>
        )}
        {parsed.map((line, i) => (
          <div key={i} className="animate-fade-in flex gap-2 border-b border-edge/50 py-1.5 last:border-0">
            <span className="shrink-0 text-zinc-600">
              {line.timestamp ? line.timestamp.toLocaleTimeString() : ''}
            </span>
            {line.level && <span className={`w-16 shrink-0 font-semibold ${levelColor(line.level)}`}>{line.level}</span>}
            <span className="break-all text-zinc-300">{line.message}</span>
          </div>
        ))}
      </div>

      {paused && (
        <button
          onClick={resume}
          className="m-3 min-h-[44px] rounded-lg bg-accent/20 text-sm font-medium text-accent hover:bg-accent/30"
        >
          ▼ Resume live scroll
        </button>
      )}
    </div>
  )

  if (isMobile) {
    return (
      <div className="flex min-h-[calc(100dvh-8rem)] flex-col gap-3">
        <details className="animate-fade-in rounded-xl border border-edge bg-surface p-3">
          <summary className="min-h-[44px] cursor-pointer text-sm font-medium text-zinc-300">Live metrics</summary>
          <div className="mt-3">
            <MetricsMini metrics={metrics.data ?? { error: metrics.error ?? undefined }} />
          </div>
        </details>
        {feed}
      </div>
    )
  }

  return (
    <div className="flex min-h-[calc(100dvh-9rem)] gap-4">
      {feed}
      <aside className="w-72 shrink-0 space-y-3">
        <MetricsMini metrics={metrics.data ?? { error: metrics.error ?? undefined }} />
      </aside>
    </div>
  )
}
