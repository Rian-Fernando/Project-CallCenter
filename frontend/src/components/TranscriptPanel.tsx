/** Live transcript with citations, confidence, and transfer cards. */

import { useEffect, useRef } from 'react'
import type { TranscriptLine } from '../services/types'
import { ActionBadge, ConfidenceBadge, SourceCard, TypingDots } from './ui'

export function TranscriptPanel({ lines }: { lines: TranscriptLine[] }) {
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [lines])

  if (lines.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div>
          <p className="text-sm text-slate-400 dark:text-slate-500">
            The conversation will appear here.
          </p>
          <p className="mt-1 text-xs text-slate-400 dark:text-slate-600">
            Every answer shows the Village sources it came from.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="thin-scroll h-full space-y-4 overflow-y-auto px-4 py-4 text-slate-700 dark:text-slate-300">
      {lines.map((line) => (
        <div key={line.id} className="animate-fade-up">
          {line.role === 'resident' ? (
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-village-700 px-4 py-2.5
                              text-sm text-white shadow-sm">
                {line.text}
              </div>
            </div>
          ) : (
            <div className="max-w-[92%]">
              <div className="rounded-2xl rounded-bl-sm border border-slate-200 bg-white
                              px-4 py-3 text-sm leading-relaxed shadow-sm
                              dark:border-slate-700 dark:bg-slate-900">
                {line.text ? (
                  <p className="whitespace-pre-wrap text-slate-800 dark:text-slate-100">
                    {line.text}
                  </p>
                ) : (
                  <TypingDots />
                )}

                {line.safetyNotice && (
                  <p className="mt-2 rounded-md bg-rose-50 px-2 py-1 text-xs font-semibold
                                text-rose-700 dark:bg-rose-950 dark:text-rose-300">
                    {line.safetyNotice}
                  </p>
                )}

                {/* Post-hoc verification failed on text already spoken. */}
                {line.groundingFailed && (
                  <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs
                                text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                    This response could not be fully verified against Village
                    sources. Please confirm with the department before relying on it.
                  </p>
                )}
              </div>

              {!line.pending && (line.action || line.departmentName) && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {line.action && <ActionBadge action={line.action} />}
                  {line.departmentName && (
                    <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs
                                     font-medium text-slate-600
                                     dark:bg-slate-800 dark:text-slate-300">
                      {line.departmentName}
                    </span>
                  )}
                  <ConfidenceBadge level={line.confidenceLevel} score={line.confidence} />
                  {line.timings?.total_ms != null && (
                    <span className="font-mono text-[10px] text-slate-400">
                      {(line.timings.total_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                </div>
              )}

              {line.sources && line.sources.length > 0 && (
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {line.sources.slice(0, 4).map((source, i) => (
                    <SourceCard key={`${line.id}-${i}`} source={source} />
                  ))}
                </div>
              )}

              {line.escalation && <TransferCard escalation={line.escalation} />}
            </div>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

/** Simulated department transfer (§11). */
function TransferCard({ escalation }: { escalation: NonNullable<TranscriptLine['escalation']> }) {
  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-village-300
                    bg-village-50 dark:border-village-700 dark:bg-village-950/60">
      <div className="flex items-center justify-between border-b border-village-200
                      bg-village-100 px-4 py-2 dark:border-village-800 dark:bg-village-900/60">
        <p className="text-xs font-bold uppercase tracking-wider text-village-800
                      dark:text-village-200">
          Transfer to {escalation.department_name}
        </p>
        <span className="rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-semibold
                         uppercase text-village-700 dark:bg-black/30 dark:text-village-300">
          Simulated
        </span>
      </div>
      <dl className="space-y-2 px-4 py-3 text-xs">
        <Row label="Reason" value={escalation.reason} />
        <Row label="Caller question" value={escalation.caller_question} />
        <Row label="Recommended action" value={escalation.recommended_action} />
        {escalation.conversation_summary && (
          <Row label="Summary" value={escalation.conversation_summary} clamp />
        )}
      </dl>
      <p className="border-t border-village-200 px-4 py-2 text-[11px] text-village-700
                    dark:border-village-800 dark:text-village-400">
        In production this maps to a SIP transfer. No call was placed.
      </p>
    </div>
  )
}

function Row({ label, value, clamp }: { label: string; value: string; clamp?: boolean }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2">
      <dt className="font-semibold text-village-700 dark:text-village-400">{label}</dt>
      <dd className={`text-slate-700 dark:text-slate-300 ${clamp ? 'line-clamp-3' : ''}`}>
        {value}
      </dd>
    </div>
  )
}
