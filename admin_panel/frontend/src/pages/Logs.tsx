import { useState } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type LogsResponse = { lines?: string[]; error?: string }

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'] as const
type Level = (typeof LEVELS)[number]

export function Logs() {
  const [minutes, setMinutes] = useState(30)
  const [level, setLevel] = useState<Level>('ALL')
  const [limit, setLimit] = useState(100)

  const { data, error, loading } = usePolling<LogsResponse>(
    () => apiGet(`/api/logs?minutes=${minutes}&level=${level}&limit=${limit}`),
    15000,
    [minutes, level, limit],
  )

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-3 text-sm text-zinc-400">
        <label className="flex items-center gap-2">
          Minutes
          <input
            type="number"
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="min-h-[44px] w-16 rounded-lg border border-edge bg-surface px-2 text-zinc-200"
          />
        </label>
        <label className="flex items-center gap-2">
          Level
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as Level)}
            className="min-h-[44px] rounded-lg border border-edge bg-surface px-2 text-zinc-200"
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Limit
          <input
            type="number"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="min-h-[44px] w-16 rounded-lg border border-edge bg-surface px-2 text-zinc-200"
          />
        </label>
      </div>
      {error && <Banner kind="error" message={error} />}
      {data?.error && <Banner kind="error" message={data.error} />}
      {loading && !data && <p className="py-10 text-center text-zinc-600">Loading logs…</p>}
      {data?.lines && (
        <pre className="max-h-[70vh] overflow-auto rounded-xl border border-edge bg-surface p-3 text-xs text-zinc-300">
          {data.lines.join('\n')}
        </pre>
      )}
    </div>
  )
}
