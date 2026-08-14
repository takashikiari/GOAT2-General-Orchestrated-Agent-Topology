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
      <div className="mb-3 flex gap-3 text-sm">
        <label>
          Minutes:{' '}
          <input
            type="number"
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="w-16 rounded border px-1"
          />
        </label>
        <label>
          Level:{' '}
          <select value={level} onChange={(e) => setLevel(e.target.value as Level)}>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label>
          Limit:{' '}
          <input
            type="number"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="w-16 rounded border px-1"
          />
        </label>
      </div>
      {error && <Banner kind="error" message={error} />}
      {data?.error && <Banner kind="error" message={data.error} />}
      {loading && !data && <div>Loading…</div>}
      {data?.lines && (
        <pre className="max-h-[70vh] overflow-auto rounded border bg-gray-900 p-3 text-xs text-gray-100">
          {data.lines.join('\n')}
        </pre>
      )}
    </div>
  )
}
