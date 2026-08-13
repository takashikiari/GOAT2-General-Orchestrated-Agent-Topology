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
