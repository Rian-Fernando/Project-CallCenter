import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Demo from './pages/Demo'
import Privacy from './pages/Privacy'
import Receptionist from './pages/Receptionist'
import Review from './pages/Review'
import { api } from './services/api'
import type { Health } from './services/types'

const NAV = [
  { to: '/', label: 'Receptionist', end: true },
  { to: '/demo', label: 'Demo' },
  { to: '/admin', label: 'Dashboard' },
  { to: '/admin/review', label: 'Review queue' },
  { to: '/admin/privacy', label: 'Privacy' },
]

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [dark, setDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  )

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  useEffect(() => {
    const poll = () => api.health().then(setHealth).catch(() => setHealth(null))
    poll()
    const timer = setInterval(poll, 30000)
    return () => clearInterval(timer)
  }, [])

  const dot =
    health?.status === 'ok' ? 'bg-emerald-500'
      : health?.status === 'degraded' ? 'bg-amber-500'
        : 'bg-rose-500'

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90
                         backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg
                            bg-village-800 text-sm font-bold text-white">
              GC
            </div>
            <div className="leading-tight">
              <p className="text-sm font-semibold">Village of Garden City</p>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                AI Receptionist · Proof of Concept
              </p>
            </div>
          </div>

          <nav className="flex flex-1 flex-wrap gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    isActive
                      ? 'bg-village-50 text-village-800 dark:bg-village-950 dark:text-village-200'
                      : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <span
              className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"
              title={
                health
                  ? Object.entries(health.services)
                      .map(([k, v]) => `${k}: ${v.state}`)
                      .join('\n')
                  : 'Backend unreachable'
              }
            >
              <span className={`h-2 w-2 rounded-full ${dot}`} />
              {health?.status ?? 'offline'}
            </span>
            <button
              onClick={() => setDark(!dark)}
              aria-label="Toggle dark mode"
              className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100
                         dark:text-slate-400 dark:hover:bg-slate-800"
            >
              {dark ? '☀' : '☾'}
            </button>
          </div>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Receptionist />} />
          <Route path="/demo" element={<Demo />} />
          <Route path="/admin" element={<Dashboard />} />
          <Route path="/admin/review" element={<Review />} />
          <Route path="/admin/privacy" element={<Privacy />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="mx-auto max-w-7xl px-4 py-8 text-center text-xs
                         text-slate-400 dark:text-slate-600">
        <p>
          Proof of concept. Not an official Village of Garden City service.
          Sources marked <strong>DEMO DATA</strong> are placeholders and are not
          official Village information.
        </p>
      </footer>
    </div>
  )
}
