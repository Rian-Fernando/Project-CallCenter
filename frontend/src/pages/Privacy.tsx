/**
 * Privacy and retention controls (§17).
 *
 * Municipal call transcripts are sensitive. This page makes the retention
 * policy visible and gives an administrator direct, real deletion controls.
 */

import { useCallback, useEffect, useState } from 'react'
import { Card, ErrorBanner, SectionTitle, Spinner } from '../components/ui'
import { api } from '../services/api'
import type { ConversationSummary } from '../services/types'

const RETENTION_OPTIONS = [
  { days: 7, label: '7 days', hint: 'Prototype default' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 0, label: 'Never delete', hint: 'Not recommended' },
]

export default function Privacy() {
  const [settings, setSettings] = useState<Record<string, any> | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmingAll, setConfirmingAll] = useState(false)

  const load = useCallback(() => {
    api.privacySettings().then(setSettings).catch((e) => setError(e.message))
    api.conversations(25).then(setConversations).catch(() => {})
  }, [])

  useEffect(load, [load])

  const setRetention = async (days: number) => {
    setBusy(true)
    try {
      const result = await api.setRetention(days)
      setNotice(result.message)
      load()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const purge = async () => {
    setBusy(true)
    try {
      const result: any = await api.purge()
      setNotice(
        `Purge complete — ${result.deleted_conversations} conversation(s) and ` +
        `${result.deleted_turns} turn(s) permanently deleted.`,
      )
      load()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const deleteAll = async () => {
    setBusy(true)
    try {
      const result: any = await api.deleteAllConversations()
      setNotice(`Deleted all ${result.deleted_conversations} conversation(s).`)
      setConfirmingAll(false)
      load()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const deleteOne = async (id: string) => {
    try {
      await api.deleteConversation(id)
      setNotice('Conversation permanently deleted.')
      load()
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">
          Privacy &amp; retention
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          What this system stores, for how long, and how to delete it.
        </p>
      </div>

      {error && <ErrorBanner message={error} />}
      {notice && (
        <div className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800
                        dark:bg-emerald-950 dark:text-emerald-300">
          {notice}
        </div>
      )}

      {!settings ? (
        <div className="flex items-center gap-2 py-12 text-slate-400">
          <Spinner /> <span className="text-sm">Loading…</span>
        </div>
      ) : (
        <>
          <Card className="p-5">
            <SectionTitle hint="Conversations older than this are permanently deleted">
              Retention period
            </SectionTitle>
            <div className="flex flex-wrap gap-2">
              {RETENTION_OPTIONS.map((option) => (
                <button
                  key={option.days}
                  onClick={() => setRetention(option.days)}
                  disabled={busy}
                  className={`rounded-lg border px-4 py-2 text-sm transition ${
                    settings.retention_days === option.days
                      ? 'border-village-600 bg-village-700 text-white'
                      : 'border-slate-300 text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800'
                  }`}
                >
                  {option.label}
                  {option.hint && (
                    <span className="ml-1 text-[10px] opacity-70">({option.hint})</span>
                  )}
                </button>
              ))}
            </div>
            <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
              Changing this affects the running process. Set{' '}
              <code className="font-mono">RETENTION_DAYS</code> in{' '}
              <code className="font-mono">.env</code> to make it permanent.
            </p>
          </Card>

          <Card className="p-5">
            <SectionTitle>What is and isn't stored</SectionTitle>
            <ul className="space-y-2 text-sm text-slate-600 dark:text-slate-300">
              {(settings.notes ?? []).map((note: string, i: number) => (
                <li key={i} className="flex gap-2">
                  <span className="mt-0.5 text-emerald-600">✓</span>
                  <span>{note}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              Transcripts are stored in {settings.transcript_location} at{' '}
              <code className="font-mono">data/gardencity.db</code>.
            </p>
          </Card>

          <Card className="p-5">
            <SectionTitle hint="These actions are immediate and cannot be undone">
              Deletion controls
            </SectionTitle>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={purge}
                disabled={busy}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm
                           text-slate-700 hover:bg-slate-50 disabled:opacity-40
                           dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Purge past retention window
              </button>
              {!confirmingAll ? (
                <button
                  onClick={() => setConfirmingAll(true)}
                  disabled={busy}
                  className="rounded-lg border border-rose-300 px-4 py-2 text-sm
                             text-rose-700 hover:bg-rose-50 disabled:opacity-40
                             dark:border-rose-800 dark:text-rose-300 dark:hover:bg-rose-950"
                >
                  Delete all conversations
                </button>
              ) : (
                <div className="flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2
                                dark:bg-rose-950">
                  <span className="text-xs text-rose-800 dark:text-rose-300">
                    Delete every conversation permanently?
                  </span>
                  <button onClick={deleteAll} disabled={busy}
                          className="rounded bg-rose-600 px-3 py-1 text-xs font-semibold text-white">
                    Yes, delete
                  </button>
                  <button onClick={() => setConfirmingAll(false)}
                          className="rounded px-2 py-1 text-xs text-slate-600 dark:text-slate-300">
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </Card>

          <Card className="p-5">
            <SectionTitle hint="Most recent 25">Stored conversations</SectionTitle>
            {conversations.length === 0 ? (
              <p className="text-xs text-slate-400">No conversations stored.</p>
            ) : (
              <div className="thin-scroll max-h-96 overflow-y-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-white text-slate-500 dark:bg-slate-900">
                    <tr>
                      <th className="py-2 font-medium">Started</th>
                      <th className="py-2 font-medium">Department</th>
                      <th className="py-2 font-medium">Outcome</th>
                      <th className="py-2 text-right font-medium">Turns</th>
                      <th className="py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {conversations.map((c) => (
                      <tr key={c.id}
                          className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-2 text-slate-600 dark:text-slate-300">
                          {new Date(c.started_at).toLocaleString()}
                        </td>
                        <td className="py-2 text-slate-600 dark:text-slate-300">
                          {c.primary_department ?? '—'}
                        </td>
                        <td className="py-2">
                          <span className={c.escalated ? 'text-rose-600' : 'text-emerald-600'}>
                            {c.resolution.replace('_', ' ')}
                          </span>
                        </td>
                        <td className="py-2 text-right tabular-nums text-slate-500">
                          {c.turn_count}
                        </td>
                        <td className="py-2 text-right">
                          <button
                            onClick={() => deleteOne(c.id)}
                            className="text-rose-600 underline-offset-2 hover:underline"
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
