/**
 * Demo mode (§26).
 *
 * One-click scenarios that need no microphone — for presenting on a laptop
 * where mic permission is unavailable or a room is too noisy. Scenario 5 is a
 * two-turn sequence that shares a session id, proving conversation memory.
 */

import { useState } from 'react'
import { TranscriptPanel } from '../components/TranscriptPanel'
import { Card, ErrorBanner, SectionTitle, Spinner } from '../components/ui'
import { api } from '../services/api'
import type { TranscriptLine } from '../services/types'

const uid = () => Math.random().toString(36).slice(2, 10)

interface Scenario {
  id: number
  title: string
  turns: string[]
  expectation: string
  demonstrates: string
}

const SCENARIOS: Scenario[] = [
  {
    id: 1,
    title: 'Report a pothole',
    turns: ['Where do I report a pothole?'],
    expectation: 'Routes to Public Works',
    demonstrates: 'Deterministic keyword routing with no LLM call',
  },
  {
    id: 2,
    title: 'Garbage collection schedule',
    turns: ['When is garbage collection?'],
    expectation: 'Answers from the Village knowledge base, with citation',
    demonstrates: 'Grounded RAG answer from official gardencityny.net content',
  },
  {
    id: 3,
    title: 'Building permit',
    turns: ['I need a building permit'],
    expectation: 'Routes to the Building Department',
    demonstrates: 'Department routing plus a cited answer',
  },
  {
    id: 4,
    title: 'Question outside the knowledge base',
    turns: ['What is the airspeed velocity of an unladen swallow?'],
    expectation: 'Declines to answer and offers a transfer',
    demonstrates: 'Refusal instead of fabrication — the core safety behavior',
  },
  {
    id: 5,
    title: 'Follow-up using conversation memory',
    turns: ['I have a question about garbage pickup.', 'When is mine?'],
    expectation: '"Mine" is understood as garbage collection',
    demonstrates: 'Session-scoped context resolution across turns',
  },
]

export default function Demo() {
  const [lines, setLines] = useState<TranscriptLine[]>([])
  const [running, setRunning] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (scenario: Scenario) => {
    setRunning(scenario.id)
    setError(null)
    setLines([])

    // A fresh session per scenario so scenario 5's memory demo is honest.
    let sessionId: string | undefined
    try {
      for (const turn of scenario.turns) {
        setLines((prev) => [...prev, { id: uid(), role: 'resident', text: turn }])
        const pendingId = uid()
        setLines((prev) => [
          ...prev,
          { id: pendingId, role: 'assistant', text: '', pending: true },
        ])

        const result = await api.chat(turn, sessionId, 'demo')
        sessionId = result.session_id

        setLines((prev) =>
          prev.map((line) =>
            line.id === pendingId
              ? {
                  ...line,
                  text: result.answer,
                  pending: false,
                  action: result.action,
                  confidence: result.confidence,
                  confidenceLevel: result.confidence_level,
                  department: result.department,
                  departmentName: result.department_name,
                  sources: result.sources,
                  escalation: result.escalation,
                  safetyNotice: result.safety_notice,
                  timings: result.timings,
                }
              : line,
          ),
        )
      }
    } catch (err: any) {
      setError(err?.message ?? 'The scenario could not run.')
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-white">
          Demo scenarios
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
          Scripted questions that exercise routing, retrieval, refusal, and
          conversation memory. No microphone required — useful when presenting.
        </p>
      </div>

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} hint="Is the backend running on port 8000?" />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="space-y-3">
          {SCENARIOS.map((scenario) => (
            <Card key={scenario.id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                    {scenario.id}. {scenario.title}
                  </p>
                  <p className="mt-1 text-xs italic text-slate-500 dark:text-slate-400">
                    {scenario.turns.map((t) => `"${t}"`).join('  →  ')}
                  </p>
                  <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
                    <span className="font-medium">Expected:</span> {scenario.expectation}
                  </p>
                  <p className="mt-0.5 text-[11px] text-village-600 dark:text-village-400">
                    {scenario.demonstrates}
                  </p>
                </div>
                <button
                  onClick={() => run(scenario)}
                  disabled={running !== null}
                  className="flex shrink-0 items-center gap-1.5 rounded-lg bg-village-700
                             px-3 py-1.5 text-xs font-semibold text-white transition
                             hover:bg-village-800 disabled:opacity-40"
                >
                  {running === scenario.id ? <Spinner /> : null}
                  {running === scenario.id ? 'Running' : 'Run'}
                </button>
              </div>
            </Card>
          ))}
        </div>

        <section className="flex h-[640px] flex-col overflow-hidden rounded-2xl border
                            border-slate-200 bg-slate-50 shadow-sm
                            dark:border-slate-700 dark:bg-slate-950">
          <header className="border-b border-slate-200 bg-white px-4 py-3
                             dark:border-slate-700 dark:bg-slate-900">
            <SectionTitle>Result</SectionTitle>
          </header>
          <div className="min-h-0 flex-1">
            <TranscriptPanel lines={lines} />
          </div>
        </section>
      </div>
    </div>
  )
}
