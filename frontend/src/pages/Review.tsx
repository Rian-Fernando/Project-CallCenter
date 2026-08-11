/**
 * Human-in-the-loop knowledge review (§14, §15).
 *
 * The queue shows what the AI could not answer, along with the evidence it
 * tried and the confidence signals that failed. An administrator writes the
 * answer and approves it; only then does it enter the knowledge base.
 *
 * The AI cannot reach this flow. That is the point.
 */

import { useCallback, useEffect, useState } from 'react'
import { Card, EmptyState, ErrorBanner, SectionTitle, Spinner } from '../components/ui'
import { api } from '../services/api'
import type { Department, Unanswered } from '../services/types'

export default function Review() {
  const [items, setItems] = useState<Unanswered[] | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [selected, setSelected] = useState<Unanswered | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [answer, setAnswer] = useState('')
  const [department, setDepartment] = useState('general')
  const [sourceTitle, setSourceTitle] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [isOfficial, setIsOfficial] = useState(false)

  const load = useCallback(() => {
    api.unanswered()
      .then((rows) => setItems(rows.filter((r) => r.status !== 'answered')))
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load()
    api.departments().then(setDepartments).catch(() => {})
  }, [load])

  const open = (item: Unanswered) => {
    setSelected(item)
    setAnswer('')
    setDepartment(item.detected_department ?? 'general')
    setSourceTitle('')
    setSourceUrl('')
    setIsOfficial(false)
    setNotice(null)
  }

  const approve = async () => {
    if (!selected || !answer.trim()) return
    setSaving(true)
    setNotice(null)
    try {
      const result = await api.approveKnowledge({
        question: selected.question,
        answer: answer.trim(),
        department,
        source_title: sourceTitle || null,
        source_url: sourceUrl || null,
        is_official: isOfficial,
        unanswered_id: selected.id,
      })
      setNotice(result.message)
      if (result.ok) {
        setSelected(null)
        load()
      }
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const dismiss = async (item: Unanswered) => {
    try {
      await api.reviewQuestion(item.id, 'dismissed', 'Not a knowledge gap.')
      load()
      if (selected?.id === item.id) setSelected(null)
    } catch (err: any) {
      setError(err.message)
    }
  }

  if (error) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8">
        <ErrorBanner message={error} hint="Is the backend running on port 8000?" />
      </div>
    )
  }

  if (!items) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-400">
        <Spinner /> <span className="ml-2 text-sm">Loading review queue…</span>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">
          Questions requiring review
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
          Questions the assistant could not answer confidently. Approving an
          answer here adds it to the knowledge base immediately. The assistant
          can never do this on its own.
        </p>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Nothing awaiting review"
          hint="Unanswered questions appear here automatically when the AI escalates."
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          {/* ---- queue ---- */}
          <div className="space-y-3">
            {items.map((item) => (
              <Card key={item.id}
                    className={`cursor-pointer p-4 transition hover:border-village-400 ${
                      selected?.id === item.id ? 'ring-2 ring-village-500' : ''
                    }`}>
                <button onClick={() => open(item)} className="w-full text-left">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                      {item.question}
                    </p>
                    {item.occurrence_count > 1 && (
                      <span className="shrink-0 rounded-full bg-rose-100 px-2 py-0.5
                                       text-[10px] font-bold text-rose-700
                                       dark:bg-rose-950 dark:text-rose-300">
                        asked {item.occurrence_count}×
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-medium
                                     text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      {item.detected_department ?? 'unrouted'}
                    </span>
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 font-medium
                                     text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                      {item.status.replace('_', ' ')}
                    </span>
                    {item.confidence_score != null && (
                      <span className="font-mono text-slate-400">
                        conf {item.confidence_score.toFixed(2)}
                      </span>
                    )}
                    <span className="text-slate-400">
                      {new Date(item.last_asked_at).toLocaleDateString()}
                    </span>
                  </div>
                </button>
                <button
                  onClick={() => dismiss(item)}
                  className="mt-2 text-[11px] text-slate-400 underline-offset-2 hover:underline"
                >
                  Dismiss — not a knowledge gap
                </button>
              </Card>
            ))}
          </div>

          {/* ---- editor ---- */}
          <div className="lg:sticky lg:top-6 lg:self-start">
            {!selected ? (
              <EmptyState title="Select a question to review" />
            ) : (
              <Card className="p-5">
                <SectionTitle hint="Approved answers become searchable immediately">
                  Review and approve
                </SectionTitle>

                <div className="mb-4 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Resident asked
                  </p>
                  <p className="mt-1 text-sm text-slate-800 dark:text-slate-100">
                    {selected.question}
                  </p>
                </div>

                {selected.attempted_sources && selected.attempted_sources.length > 0 && (
                  <details className="mb-4 rounded-lg border border-slate-200 p-3 text-xs
                                      dark:border-slate-700">
                    <summary className="cursor-pointer font-medium text-slate-600 dark:text-slate-300">
                      What the assistant found ({selected.attempted_sources.length} sources)
                    </summary>
                    <ul className="mt-2 space-y-1">
                      {selected.attempted_sources.map((source, i) => (
                        <li key={i} className="flex justify-between gap-2 text-slate-500">
                          <span className="truncate">{source.title}</span>
                          <span className="shrink-0 font-mono text-slate-400">
                            {source.score?.toFixed?.(2)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}

                {selected.confidence_signals && (
                  <details className="mb-4 rounded-lg border border-slate-200 p-3 text-xs
                                      dark:border-slate-700">
                    <summary className="cursor-pointer font-medium text-slate-600 dark:text-slate-300">
                      Why it escalated (confidence signals)
                    </summary>
                    <pre className="thin-scroll mt-2 max-h-48 overflow-auto rounded bg-slate-50
                                    p-2 text-[10px] leading-relaxed text-slate-600
                                    dark:bg-slate-900 dark:text-slate-400">
{JSON.stringify(selected.confidence_signals, null, 2)}
                    </pre>
                  </details>
                )}

                <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                  Approved answer
                </label>
                <textarea
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  rows={5}
                  placeholder="Write the verified answer a resident should hear…"
                  className="w-full rounded-lg border border-slate-300 bg-white p-3 text-sm
                             text-slate-800 placeholder:text-slate-400
                             focus:border-village-500 focus:outline-none
                             dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                />

                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                      Department
                    </label>
                    <select
                      value={department}
                      onChange={(e) => setDepartment(e.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2
                                 text-sm dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    >
                      {departments.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                      Source title (optional)
                    </label>
                    <input
                      value={sourceTitle}
                      onChange={(e) => setSourceTitle(e.target.value)}
                      placeholder="e.g. Sanitation Collection Schedule"
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2
                                 text-sm dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                    />
                  </div>
                </div>

                <label className="mb-1 mt-3 block text-xs font-medium text-slate-600 dark:text-slate-300">
                  Source URL (optional)
                </label>
                <input
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://www.gardencityny.net/…"
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                             dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
                />

                <label className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 p-3
                                  dark:bg-amber-950/40">
                  <input
                    type="checkbox"
                    checked={isOfficial}
                    onChange={(e) => setIsOfficial(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span className="text-xs text-amber-900 dark:text-amber-200">
                    <strong>Mark as official Village information.</strong> Only tick
                    this if you have verified the answer against an official source.
                    Unticked answers are labeled <em>DEMO DATA</em> wherever they appear.
                  </span>
                </label>

                {notice && (
                  <p className="mt-3 rounded-lg bg-emerald-50 p-2 text-xs text-emerald-800
                                dark:bg-emerald-950 dark:text-emerald-300">
                    {notice}
                  </p>
                )}

                <div className="mt-4 flex gap-2">
                  <button
                    onClick={approve}
                    disabled={!answer.trim() || saving}
                    className="flex items-center gap-2 rounded-lg bg-village-700 px-4 py-2
                               text-sm font-semibold text-white transition
                               hover:bg-village-800 disabled:opacity-40"
                  >
                    {saving && <Spinner />}
                    Approve &amp; add to knowledge base
                  </button>
                  <button
                    onClick={() => setSelected(null)}
                    className="rounded-lg border border-slate-300 px-4 py-2 text-sm
                               text-slate-600 dark:border-slate-600 dark:text-slate-300"
                  >
                    Cancel
                  </button>
                </div>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
