declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        initData: string
        ready: () => void
      }
    }
  }
}

/**
 * Resolves the Telegram WebApp initData for the current session.
 *
 * In a real Telegram Mini App webview, window.Telegram.WebApp.initData is
 * always present. The dev-mode fallback only activates when import.meta.env.DEV
 * is true — Vite hardcodes DEV to false in `vite build`, so this branch is
 * unreachable in a production bundle regardless of what's on disk.
 */
export function getInitData(): string {
  const real = window.Telegram?.WebApp?.initData
  if (real) return real
  if (import.meta.env.DEV && import.meta.env.VITE_DEV_INIT_DATA) {
    return import.meta.env.VITE_DEV_INIT_DATA
  }
  return ''
}
