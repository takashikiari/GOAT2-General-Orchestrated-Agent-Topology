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

// The only three real values memory/observability.py's source_tier ever
// takes — always shown, even at 0%, so a tier that simply hasn't been hit
// yet doesn't silently disappear from the panel instead of reading "0%".
const KNOWN_TIERS = ['working', 'permanent', 'episodic'] as const

function tierEntries(rates: Record<string, number> | undefined): [string, number][] {
  const present = rates ?? {}
  const extra = Object.keys(present).filter((k) => !(KNOWN_TIERS as readonly string[]).includes(k))
  return [...KNOWN_TIERS, ...extra].map((tier) => [tier, present[tier] ?? 0])
}

type Level = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'
const LEVELS: { id: Level; label: string }[] = [
  { id: 'ALL', label: 'All' },
  { id: 'INFO', label: 'Info' },
  { id: 'WARNING', label: 'Warn' },
  { id: 'ERROR', label: 'Error' },
]

type ParsedLine = { raw: string; timestamp: Date | null; level: string; message: string }
// A group is one structured log line plus any unstructured lines that follow
// it (stack traces, multi-line payloads) — rendered as de-emphasized detail
// under the entry that produced them, instead of every raw line getting the
// same visual weight as a real log event.
type LogGroup = { key: number; header: ParsedLine | null; extra: string[] }

const LOG_LINE_RE = /^(\S+)\s+(\S+)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$/
const SCROLL_BOTTOM_THRESHOLD_PX = 32

export function parseLogLine(raw: string): ParsedLine {
  const match = LOG_LINE_RE.exec(raw)
  if (!match) return { raw, timestamp: null, level: '', message: raw }
  const [, ts, , level, message] = match
  const parsedTs = new Date(ts)
  return { raw, timestamp: Number.isNaN(parsedTs.getTime()) ? null : parsedTs, level, message }
}

function groupLines(lines: string[]): LogGroup[] {
  const groups: LogGroup[] = []
  for (const raw of lines) {
    const parsed = parseLogLine(raw)
    if (parsed.level) {
      groups.push({ key: groups.length, header: parsed, extra: [] })
    } else if (groups.length > 0) {
      groups[groups.length - 1].extra.push(raw)
    } else {
      groups.push({ key: groups.length, header: null, extra: [raw] })
    }
  }
  return groups
}

function levelBadgeClass(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'border-danger/40 bg-danger/15 text-danger'
  if (level === 'WARNING') return 'border-warn/40 bg-warn/15 text-warn'
  if (level === 'DEBUG') return 'border-zinc-600/40 bg-zinc-700/20 text-zinc-400'
  return 'border-accent/40 bg-accent/15 text-accent'
}

function levelBorderClass(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'border-l-danger'
  if (level === 'WARNING') return 'border-l-warn'
  if (level === 'DEBUG') return 'border-l-zinc-600'
  return 'border-l-accent'
}

function LiveBadge({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-accent-green/30 bg-accent-green/10 px-3 py-1.5 text-xs shadow-glow-green">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-pulse-live rounded-full bg-accent-green" />
      </span>
      <span className="font-bold tracking-wide text-accent-green">LIVE</span>
      <span className="text-zinc-400">· {count} in last min</span>
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-edge bg-gradient-to-br from-surface to-surface2 p-4 shadow-card">
      <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1.5 text-2xl font-bold text-accent [text-shadow:0_0_20px_rgba(34,211,238,0.35)]">
        {value}
      </div>
    </div>
  )
}

function MetricsMini({ metrics }: { metrics: MetricsSlice }) {
  if (metrics.error) return <Banner kind="error" message={metrics.error} />
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <StatCard label="Processed" value={metrics.total_requests !== undefined ? String(metrics.total_requests) : '—'} />
        <StatCard
          label="Avg latency"
          value={metrics.avg_latency_total !== undefined ? `${metrics.avg_latency_total.toFixed(2)}s` : '—'}
        />
      </div>
      <div className="rounded-xl border border-edge bg-surface p-4 shadow-card">
        <div className="mb-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Memory tier hit rate</div>
        <div className="space-y-2.5">
          {tierEntries(metrics.tier_hit_rates).map(([tier, rate]) => (
            <div key={tier} className="flex items-center gap-2 text-xs">
              <span className="w-20 shrink-0 truncate text-zinc-300" title={tier}>
                {tier}
              </span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface2">
                <div
                  className={`h-full rounded-full ${rate > 0 ? 'bg-gradient-to-r from-accent-green to-accent shadow-glow' : 'bg-zinc-700'}`}
                  style={{ width: `${Math.max(Math.min(rate * 100, 100), rate > 0 ? 2 : 0)}%` }}
                />
              </div>
              <span className="w-10 text-right font-medium text-zinc-300">{(rate * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function LogEntry({ group }: { group: LogGroup }) {
  const { header, extra } = group
  return (
    <div
      className={`animate-fade-in border-l-2 py-2 pl-3 ${header ? levelBorderClass(header.level) : 'border-l-zinc-700'}`}
    >
      {header && (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="shrink-0 font-mono text-[11px] text-zinc-500">
            {header.timestamp ? header.timestamp.toLocaleTimeString() : ''}
          </span>
          <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-bold ${levelBadgeClass(header.level)}`}>
            {header.level}
          </span>
          <span className="break-all text-sm text-zinc-200">{header.message}</span>
        </div>
      )}
      {extra.length > 0 && (
        <pre className={`overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-zinc-400 ${header ? 'mt-1.5 pl-1' : ''}`}>
          {extra.join('\n')}
        </pre>
      )}
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
  const groups = useMemo(() => groupLines(logs.data?.lines ?? []), [logs.data])

  const eventsLastMinute = useMemo(() => {
    const cutoff = Date.now() - 60_000
    return parsed.filter((p) => p.timestamp !== null && p.timestamp.getTime() >= cutoff).length
  }, [parsed])

  useEffect(() => {
    if (paused) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [groups, paused])

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
    <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-edge bg-surface shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge bg-radial-fade p-3">
        <LiveBadge count={eventsLastMinute} />
        <div className="flex gap-1 rounded-full border border-edge bg-base/50 p-1">
          {LEVELS.map((l) => (
            <button
              key={l.id}
              onClick={() => setLevel(l.id)}
              className={`min-h-[36px] rounded-full px-3 text-xs font-semibold transition-colors ${
                level === l.id ? 'bg-accent/20 text-accent shadow-glow' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>

      {logs.error && <div className="p-3"><Banner kind="error" message={logs.error} /></div>}
      {logs.data?.error && <div className="p-3"><Banner kind="error" message={logs.data.error} /></div>}

      <div ref={scrollRef} onScroll={onScroll} className="relative min-h-[50vh] flex-1 overflow-y-auto p-3">
        {groups.length === 0 && !logs.error && (
          <p className="py-10 text-center text-zinc-400">Waiting for activity…</p>
        )}
        {groups.map((group) => (
          <LogEntry key={group.key} group={group} />
        ))}
      </div>

      {paused && (
        <button
          onClick={resume}
          className="m-3 min-h-[44px] rounded-lg bg-accent/15 text-sm font-semibold text-accent shadow-glow hover:bg-accent/25"
        >
          ▼ Resume live scroll
        </button>
      )}
    </div>
  )

  if (isMobile) {
    return (
      <div className="flex min-h-[calc(100dvh-8rem)] flex-col gap-3">
        <details className="animate-fade-in rounded-xl border border-edge bg-surface p-3 shadow-card">
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
      <aside className="w-80 shrink-0 space-y-3">
        <MetricsMini metrics={metrics.data ?? { error: metrics.error ?? undefined }} />
      </aside>
    </div>
  )
}
