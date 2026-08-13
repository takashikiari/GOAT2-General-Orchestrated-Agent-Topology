import { useEffect, useRef, useState } from 'react'

type PollingState<T> = {
  data: T | null
  error: string | null
  loading: boolean
  refetch: () => void
}

/**
 * Fetches immediately, then every intervalMs. Restarts (clearing any
 * in-flight interval) whenever a value in deps changes — this is what stops
 * a stale poll for a previous chat_id selection from clobbering the new one.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[],
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    const run = async () => {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()
    const id = setInterval(run, intervalMs)

    return () => {
      cancelled = true
      clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps])

  return { data, error, loading, refetch: () => setTick((t) => t + 1) }
}
