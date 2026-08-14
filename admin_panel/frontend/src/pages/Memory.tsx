import { useState } from 'react'
import { apiGet } from '../api/client'
import { Banner } from '../components/Banner'
import { usePolling } from '../hooks/usePolling'

type Tier = 'facts' | 'working' | 'episodic'
type Order = 'recent' | 'oldest'

type FactsResponse = { facts?: Record<string, unknown>; error?: string }
type WorkingResponse = { messages?: unknown[]; error?: string }
type EpisodicResponse = { entries?: unknown[]; error?: string }

function useFacts() {
  return usePolling<FactsResponse>(() => apiGet('/api/memory/facts'), 15000, [])
}

function useWorking(chatId: string) {
  return usePolling<WorkingResponse | null>(
    () => (chatId ? apiGet(`/api/memory/working/${encodeURIComponent(chatId)}`) : Promise.resolve(null)),
    15000,
    [chatId],
  )
}

function useEpisodic(chatId: string, order: Order) {
  return usePolling<EpisodicResponse | null>(
    () =>
      chatId
        ? apiGet(`/api/memory/episodic/${encodeURIComponent(chatId)}?order=${order}`)
        : Promise.resolve(null),
    15000,
    [chatId, order],
  )
}

export function Memory() {
  const [tier, setTier] = useState<Tier>('facts')
  const [chatId, setChatId] = useState('')
  const [order, setOrder] = useState<Order>('recent')

  const facts = useFacts()
  const working = useWorking(tier === 'working' ? chatId : '')
  const episodic = useEpisodic(tier === 'episodic' ? chatId : '', order)

  return (
    <div>
      <div className="mb-3 flex gap-3 text-sm">
        {(['facts', 'working', 'episodic'] as Tier[]).map((t) => (
          <button
            key={t}
            onClick={() => setTier(t)}
            className={tier === t ? 'font-semibold underline' : 'text-gray-500'}
          >
            {t}
          </button>
        ))}
        {tier !== 'facts' && (
          <label>
            chat_id: <input value={chatId} onChange={(e) => setChatId(e.target.value)} className="rounded border px-1" />
          </label>
        )}
        {tier === 'episodic' && (
          <label>
            order:{' '}
            <select value={order} onChange={(e) => setOrder(e.target.value as Order)}>
              <option value="recent">recent</option>
              <option value="oldest">oldest</option>
            </select>
          </label>
        )}
      </div>

      {tier === 'facts' && (
        <>
          {facts.error && <Banner kind="error" message={facts.error} />}
          {facts.data?.error && <Banner kind="error" message={facts.data.error} />}
          {facts.data?.facts && <pre className="text-xs">{JSON.stringify(facts.data.facts, null, 2)}</pre>}
        </>
      )}
      {tier === 'working' && chatId && (
        <>
          {working.error && <Banner kind="error" message={working.error} />}
          {working.data?.error && <Banner kind="error" message={working.data.error} />}
          {working.data?.messages && (
            <pre className="text-xs">{JSON.stringify(working.data.messages, null, 2)}</pre>
          )}
        </>
      )}
      {tier === 'episodic' && chatId && (
        <>
          {episodic.error && <Banner kind="error" message={episodic.error} />}
          {episodic.data?.error && <Banner kind="error" message={episodic.data.error} />}
          {episodic.data?.entries && (
            <pre className="text-xs">{JSON.stringify(episodic.data.entries, null, 2)}</pre>
          )}
        </>
      )}
      {tier !== 'facts' && !chatId && <p className="text-gray-500">Enter a chat_id above.</p>}
    </div>
  )
}
