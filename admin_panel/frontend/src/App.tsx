import { useState } from 'react'

type Tab = 'dashboard' | 'logs' | 'memory' | 'conversations'

const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'logs', label: 'Logs' },
  { id: 'memory', label: 'Memory' },
  { id: 'conversations', label: 'Conversations' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <nav className="flex border-b bg-white">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            aria-current={activeTab === tab.id ? 'page' : undefined}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-3 text-sm font-medium ${
              activeTab === tab.id
                ? 'border-b-2 border-blue-600 text-blue-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main className="p-4">
        {activeTab === 'dashboard' && <div data-testid="panel-dashboard">Dashboard coming soon</div>}
        {activeTab === 'logs' && <div data-testid="panel-logs">Logs coming soon</div>}
        {activeTab === 'memory' && <div data-testid="panel-memory">Memory coming soon</div>}
        {activeTab === 'conversations' && <div data-testid="panel-conversations">Conversations coming soon</div>}
      </main>
    </div>
  )
}
