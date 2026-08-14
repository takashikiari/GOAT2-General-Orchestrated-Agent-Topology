import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { apiGet } from './api/client'
import {
  CloseIcon,
  ConversationsIcon,
  DashboardIcon,
  LiveIcon,
  LogsIcon,
  MemoryIcon,
  MenuIcon,
} from './components/icons'
import { useIsMobile } from './hooks/useIsMobile'
import { usePolling } from './hooks/usePolling'
import { Conversations } from './pages/Conversations'
import { Dashboard } from './pages/Dashboard'
import { Live } from './pages/Live'
import { Logs } from './pages/Logs'
import { Memory } from './pages/Memory'

type Tab = 'live' | 'dashboard' | 'logs' | 'memory' | 'conversations'
type IconComponent = (props: { className?: string }) => ReactNode

const TABS: { id: Tab; label: string; Icon: IconComponent }[] = [
  { id: 'live', label: 'Live', Icon: LiveIcon },
  { id: 'dashboard', label: 'Dashboard', Icon: DashboardIcon },
  { id: 'logs', label: 'Logs', Icon: LogsIcon },
  { id: 'memory', label: 'Memory', Icon: MemoryIcon },
  { id: 'conversations', label: 'Conversations', Icon: ConversationsIcon },
]

const NAV_BUTTON =
  'flex min-h-[44px] w-full items-center gap-3 rounded-lg border-l-2 border-transparent px-3 text-sm transition-all'
const NAV_BUTTON_ACTIVE = 'border-l-accent bg-gradient-to-r from-accent/15 to-transparent text-accent font-medium'
const NAV_BUTTON_INACTIVE = 'text-zinc-500 hover:bg-surface2 hover:text-zinc-200'

function StatusHeader({ compact = false }: { compact?: boolean }) {
  const health = usePolling<Record<string, unknown>>(() => apiGet('/api/metrics'), 10000, [])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  useEffect(() => {
    if (health.data) setLastUpdated(new Date())
  }, [health.data])

  const up = !health.error

  return (
    <div className={`flex items-center gap-2 text-xs ${compact ? 'text-zinc-500' : 'text-zinc-400'}`}>
      <span
        className={`h-2 w-2 rounded-full ${up ? 'bg-accent-green shadow-glow-green' : 'bg-danger'}`}
        aria-label={up ? 'server up' : 'server down'}
      />
      <span className={compact ? '' : 'hidden sm:inline'}>{up ? 'Online' : 'Offline'}</span>
      {lastUpdated && (
        <span className={`text-zinc-600 ${compact ? 'hidden lg:inline' : 'hidden sm:inline'}`}>
          · updated {lastUpdated.toLocaleTimeString()}
        </span>
      )}
    </div>
  )
}

function NavItems({
  activeTab,
  onSelect,
}: {
  activeTab: Tab
  onSelect: (tab: Tab) => void
}) {
  return (
    <>
      {TABS.map(({ id, label, Icon }) => (
        <button
          key={id}
          aria-current={activeTab === id ? 'page' : undefined}
          onClick={() => onSelect(id)}
          className={`${NAV_BUTTON} ${activeTab === id ? NAV_BUTTON_ACTIVE : NAV_BUTTON_INACTIVE}`}
        >
          <Icon className="h-5 w-5" />
          {label}
        </button>
      ))}
    </>
  )
}

function Sidebar({ activeTab, setActiveTab }: { activeTab: Tab; setActiveTab: (tab: Tab) => void }) {
  return (
    <nav className="fixed inset-y-0 left-0 flex w-60 flex-col border-r border-edge bg-surface">
      <div className="flex items-center gap-2.5 border-b border-edge px-4 py-5">
        <span className="h-2.5 w-2.5 shrink-0 animate-pulse-live rounded-full bg-accent shadow-glow" />
        <span className="text-sm font-bold tracking-wide text-zinc-100">GOAT 2.0</span>
      </div>
      <div className="flex-1 space-y-1 px-2 py-3">
        <NavItems activeTab={activeTab} onSelect={setActiveTab} />
      </div>
      <div className="border-t border-edge px-4 py-4">
        <StatusHeader compact />
        <p className="mt-1 text-[11px] text-zinc-700">Admin Panel · v1</p>
      </div>
    </nav>
  )
}

function MobileHeader({ activeTab, setActiveTab }: { activeTab: Tab; setActiveTab: (tab: Tab) => void }) {
  const [open, setOpen] = useState(false)
  const active = TABS.find((t) => t.id === activeTab)!

  return (
    <>
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-edge bg-surface px-3 py-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <button
          aria-label="Open navigation"
          onClick={() => setOpen(true)}
          className="flex h-11 w-11 items-center justify-center rounded-lg text-zinc-300 active:bg-surface2"
        >
          <MenuIcon className="h-6 w-6" />
        </button>
        <span className="text-sm font-medium text-zinc-200">{active.label}</span>
        <StatusHeader />
      </header>
      {open && (
        <div className="fixed inset-0 z-40 flex">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <nav className="relative flex w-72 max-w-[85vw] flex-col bg-surface pt-[max(1rem,env(safe-area-inset-top))]">
            <div className="flex items-center justify-between px-4 pb-4">
              <span className="text-sm font-semibold text-zinc-200">GOAT 2.0</span>
              <button
                aria-label="Close navigation"
                onClick={() => setOpen(false)}
                className="flex h-11 w-11 items-center justify-center rounded-lg text-zinc-400 active:bg-surface2"
              >
                <CloseIcon className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 space-y-1 px-2 pb-[max(1rem,env(safe-area-inset-bottom))]">
              <NavItems
                activeTab={activeTab}
                onSelect={(tab) => {
                  setActiveTab(tab)
                  setOpen(false)
                }}
              />
            </div>
          </nav>
        </div>
      )}
    </>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('live')
  const isMobile = useIsMobile()

  return (
    <div className="min-h-screen bg-base text-zinc-200">
      {isMobile ? (
        <MobileHeader activeTab={activeTab} setActiveTab={setActiveTab} />
      ) : (
        <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />
      )}
      <div className={isMobile ? '' : 'pl-60'}>
        <main className="mx-auto max-w-6xl px-3 py-5 pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-6">
          {TABS.map(
            ({ id }) =>
              activeTab === id && (
                <div key={id} data-testid={`panel-${id}`} className="animate-fade-in">
                  {id === 'live' && <Live />}
                  {id === 'dashboard' && <Dashboard />}
                  {id === 'logs' && <Logs />}
                  {id === 'memory' && <Memory />}
                  {id === 'conversations' && <Conversations />}
                </div>
              ),
          )}
        </main>
      </div>
    </div>
  )
}
