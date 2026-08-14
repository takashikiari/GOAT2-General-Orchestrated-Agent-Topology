type BannerProps = {
  kind: 'error' | 'warning'
  message: string
}

export function Banner({ kind, message }: BannerProps) {
  const color =
    kind === 'error'
      ? 'border-danger/30 bg-danger/10 text-danger'
      : 'border-warn/30 bg-warn/10 text-warn'
  return <div className={`animate-fade-in mb-3 rounded-lg border px-3 py-2 text-sm ${color}`}>{message}</div>
}
