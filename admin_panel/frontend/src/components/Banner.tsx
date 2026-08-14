type BannerProps = {
  kind: 'error' | 'warning'
  message: string
}

export function Banner({ kind, message }: BannerProps) {
  const color =
    kind === 'error'
      ? 'bg-red-50 text-red-800 border-red-200'
      : 'bg-yellow-50 text-yellow-800 border-yellow-200'
  return <div className={`mb-3 rounded border px-3 py-2 text-sm ${color}`}>{message}</div>
}
