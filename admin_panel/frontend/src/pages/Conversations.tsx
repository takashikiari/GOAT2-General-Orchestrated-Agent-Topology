import { useState } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type Conversation = { chat_id: string; status: 'active' | 'archived_only' }
type ConversationsResponse = { conversations: Conversation[]; warnings?: string[] }

type TimelineEntry = { tier: 'L2' | 'L3'; timestamp: number; role: string; content: string }
type ConversationDetailResponse = { chat_id: string; timeline: TimelineEntry[]; warnings?: string[] }

export function Conversations() {
  const [selected, setSelected] = useState<string | null>(null)

  const list = usePolling<ConversationsResponse>(() => apiGet('/api/conversations'), 15000, [])
  const detail = usePolling<ConversationDetailResponse | null>(
    () => (selected ? apiGet(`/api/conversations/${encodeURIComponent(selected)}`) : Promise.resolve(null)),
    15000,
    [selected],
  )

  return (
    <div className="flex flex-col gap-4 md:flex-row">
      <div className="shrink-0 md:w-64">
        {list.error && <Banner kind="error" message={list.error} />}
        {list.data?.warnings?.map((w) => (
          <Banner key={w} kind="warning" message={w} />
        ))}
        <ul className="space-y-1 rounded-xl border border-edge bg-surface p-2 text-sm">
          {list.data?.conversations.map((c) => (
            <li key={c.chat_id}>
              <button
                onClick={() => setSelected(c.chat_id)}
                className={`min-h-[44px] w-full rounded-lg px-2 text-left transition-colors ${
                  selected === c.chat_id ? 'bg-surface2 text-accent' : 'text-zinc-300 hover:bg-surface2'
                }`}
              >
                {c.chat_id} <span className="text-xs text-zinc-500">({c.status})</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex-1 rounded-xl border border-edge bg-surface p-3">
        {!selected && <p className="py-10 text-center text-zinc-600">Select a conversation.</p>}
        {selected && detail.error && <Banner kind="error" message={detail.error} />}
        {selected &&
          detail.data?.warnings?.map((w) => <Banner key={w} kind="warning" message={w} />)}
        {selected && detail.data?.timeline && (
          <ul className="animate-fade-in space-y-1.5 text-sm">
            {detail.data.timeline.map((entry, i) => (
              <li key={i} className="text-zinc-300">
                <span className="text-xs text-zinc-500">[{entry.tier}]</span>{' '}
                {entry.role && <span className="font-medium text-zinc-200">{entry.role}: </span>}
                {entry.content}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
