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
  const prevDepsRef = useRef<unknown[] | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    // Only clear stale data when `deps` itself actually changed (e.g. a
    // different chat_id was selected) — not on every interval tick, and not
    // on an unrelated re-run of this effect (intervalMs/tick), so a stale
    // value from a previous selection is never shown alongside a `loading`
    // state for a new one, without introducing a flicker on each poll.
    const prevDeps = prevDepsRef.current
    const depsChanged =
      prevDeps === undefined ||
      prevDeps.length !== deps.length ||
      prevDeps.some((d, i) => d !== deps[i])
    prevDepsRef.current = deps
    if (depsChanged) {
      setData(null)
    }

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
