/**
 * Admin dashboard (§13).
 *
 * Every figure here is read from the API, which computes it from stored
 * conversations. Nothing is hardcoded — an empty database shows zeros and an
 * explanation, not fabricated demo numbers.
 */

import { useEffect, useState } from 'react'
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, EmptyState, ErrorBanner, SectionTitle, Spinner, StatTile } from '../components/ui'
import { api } from '../services/api'
import type { Analytics, Health } from '../services/types'

const DEPT_COLORS = [
  '#2a4e7e', '#31609b', '#427ab8', '#6598cc', '#97bade',
  '#c98a1f', '#e0a63c', '#5b8c6e', '#8b6f9e',
]

export default function Dashboard() {
  const [data, setData] = useState<Analytics | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)

  useEffect(() => {
    let cancelled = false
    setData(null)
    Promise.all([api.analytics(days), api.health().catch(() => null)])
      .then(([analytics, h]) => {
        if (cancelled) return
        setData(analytics)
        setHealth(h)
        setError(null)
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [days])

  if (error) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8">
        <ErrorBanner message={error} hint="Is the backend running on port 8000?" />
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-400">
        <Spinner /> <span className="ml-2 text-sm">Loading analytics…</span>
      </div>
    )
  }

  const resolutionPct = Math.round(data.resolution_rate * 100)
  const chartData = data.by_department.map((d) => ({
    name: d.department_name.replace(' Department', '').replace('General Village Information', 'General'),
    value: d.count,
    pct: d.percentage,
  }))

  return (
    <div className="mx-auto max-w-7xl space-y-8 px-4 py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">
            Overview
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Computed from stored conversations · last {days} days
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border border-slate-200 p-1 dark:border-slate-700">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded px-3 py-1 text-xs font-medium transition ${
                days === d
                  ? 'bg-village-700 text-white'
                  : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {data.total_conversations === 0 ? (
        <EmptyState
          title="No conversations recorded yet"
          hint="Open the receptionist and ask a question — analytics populate from real calls."
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile label="Conversations" value={data.total_conversations}
                      sub={`${data.total_turns} turns`} />
            <StatTile label="AI resolved" value={`${resolutionPct}%`}
                      sub={`${data.ai_resolved} of ${data.total_conversations}`}
                      tone={resolutionPct >= 60 ? 'good' : 'warn'} />
            <StatTile label="Escalations" value={data.escalated}
                      sub={`${data.clarifying} clarifications`}
                      tone={data.escalated > data.ai_resolved ? 'warn' : 'default'} />
            <StatTile
              label="Avg response"
              value={data.avg_response_ms ? `${(data.avg_response_ms / 1000).toFixed(1)}s` : '—'}
              sub={data.p95_response_ms ? `p95 ${(data.p95_response_ms / 1000).toFixed(1)}s` : undefined}
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <SectionTitle hint="Share of turns routed to each department">
                Department activity
              </SectionTitle>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical"
                            margin={{ left: 8, right: 28, top: 4, bottom: 4 }}>
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="name" width={110}
                           tick={{ fontSize: 12, fill: 'currentColor' }}
                           axisLine={false} tickLine={false}
                           className="text-slate-500" />
                    <Tooltip
                      cursor={{ fill: 'rgba(148,163,184,0.12)' }}
                      contentStyle={{
                        borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 12,
                      }}
                      formatter={(value: any, _n: any, item: any) =>
                        [`${value} turns (${item.payload.pct}%)`, 'Volume']}
                    />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={18}>
                      {chartData.map((_, i) => (
                        <Cell key={i} fill={DEPT_COLORS[i % DEPT_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <div className="space-y-6">
              <Card className="p-5">
                <SectionTitle hint="How the confidence engine classified each turn">
                  Confidence distribution
                </SectionTitle>
                <div className="space-y-3">
                  {(['high', 'medium', 'low'] as const).map((level) => {
                    const count = data.by_confidence[level] ?? 0
                    const total = Object.values(data.by_confidence)
                      .reduce((a, b) => a + b, 0) || 1
                    const pct = Math.round((count / total) * 100)
                    const color = level === 'high'
                      ? 'bg-emerald-500'
                      : level === 'medium' ? 'bg-amber-500' : 'bg-rose-500'
                    return (
                      <div key={level}>
                        <div className="mb-1 flex justify-between text-xs">
                          <span className="capitalize text-slate-600 dark:text-slate-300">
                            {level} — {level === 'high' ? 'answered' :
                              level === 'medium' ? 'clarified' : 'escalated'}
                          </span>
                          <span className="tabular-nums text-slate-400">
                            {count} · {pct}%
                          </span>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                          <div className={`h-full rounded-full ${color}`}
                               style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
              </Card>

              <Card className="p-5">
                <SectionTitle>Most common intents</SectionTitle>
                {data.top_intents.length === 0 ? (
                  <p className="text-xs text-slate-400">No intents recorded yet.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {data.top_intents.slice(0, 6).map((intent) => (
                      <li key={intent.intent}
                          className="flex justify-between text-xs text-slate-600 dark:text-slate-300">
                        <span className="font-mono">{intent.intent}</span>
                        <span className="tabular-nums text-slate-400">{intent.count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </div>
        </>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Awaiting review" value={data.unanswered_pending}
                  sub="unanswered questions"
                  tone={data.unanswered_pending > 0 ? 'warn' : 'good'} />
        <StatTile label="Knowledge chunks" value={data.knowledge_chunks}
                  sub="indexed passages" />
        <StatTile label="Active sessions" value={data.active_sessions}
                  sub="in memory now" />
        <StatTile
          label="System"
          value={health?.status === 'ok' ? 'Healthy' : health?.status ?? '—'}
          tone={health?.status === 'ok' ? 'good'
            : health?.status === 'degraded' ? 'warn' : 'bad'}
          sub={health?.ready_for_calls ? 'ready for calls' : 'not ready'}
        />
      </div>

      {health && (
        <Card className="p-5">
          <SectionTitle hint="Each provider reports its own state and the command that fixes it">
            Service health
          </SectionTitle>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(health.services).map(([name, service]) => (
              <div key={name}
                   className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${
                    service.state === 'ok' ? 'bg-emerald-500'
                      : service.state === 'degraded' ? 'bg-amber-500' : 'bg-rose-500'
                  }`} />
                  <span className="text-xs font-semibold uppercase tracking-wide
                                   text-slate-600 dark:text-slate-300">
                    {name.replace('_', ' ')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {service.detail}
                </p>
                {service.hint && service.state !== 'ok' && (
                  <p className="mt-1 font-mono text-[10px] text-village-600 dark:text-village-400">
                    {service.hint}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
