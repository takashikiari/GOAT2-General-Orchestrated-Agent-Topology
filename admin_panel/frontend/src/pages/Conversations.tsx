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
    <div className="flex gap-4">
      <div className="w-64 shrink-0">
        {list.error && <Banner kind="error" message={list.error} />}
        {list.data?.warnings?.map((w) => (
          <Banner key={w} kind="warning" message={w} />
        ))}
        <ul className="text-sm">
          {list.data?.conversations.map((c) => (
            <li key={c.chat_id}>
              <button
                onClick={() => setSelected(c.chat_id)}
                className={`w-full text-left ${selected === c.chat_id ? 'font-semibold' : ''}`}
              >
                {c.chat_id} <span className="text-xs text-gray-400">({c.status})</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex-1">
        {!selected && <p className="text-gray-500">Select a conversation.</p>}
        {selected && detail.error && <Banner kind="error" message={detail.error} />}
        {selected &&
          detail.data?.warnings?.map((w) => <Banner key={w} kind="warning" message={w} />)}
        {selected && detail.data?.timeline && (
          <ul className="space-y-1 text-sm">
            {detail.data.timeline.map((entry, i) => (
              <li key={i}>
                <span className="text-xs text-gray-400">[{entry.tier}]</span>{' '}
                {entry.role && <span className="font-medium">{entry.role}: </span>}
                {entry.content}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
