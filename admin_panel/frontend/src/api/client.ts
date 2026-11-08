import { getInitData } from '../telegram/initData'

/**
 * A 200 response with an {error}/{warnings} body is a valid, expected shape
 * from this backend (see admin_panel/routes/*.py) — only a non-2xx HTTP
 * status is treated as a failure here.
 */
export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path, {
    headers: { 'X-Telegram-Init-Data': getInitData() },
  })
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`)
  }
  return (await resp.json()) as T
}

/** A non-2xx response; ``detail`` is FastAPI's error text when the body carries one. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(message)
  }
}

/** POST JSON (or nothing) to a route that acts; throws ApiError with the status and server detail on failure. */
export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'X-Telegram-Init-Data': getInitData(), 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    let detail = ''
    try {
      const data = (await resp.json()) as { detail?: unknown }
      detail = typeof data.detail === 'string' ? data.detail : ''
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(`${path} failed: ${resp.status}`, resp.status, detail)
  }
  return (await resp.json()) as T
}
