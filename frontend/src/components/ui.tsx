/** Small shared presentation components. */

import type { ReactNode } from 'react'
import type { Action, ConfidenceLevel, Source } from '../services/types'

export function Card({
  children, className = '', as: Tag = 'div',
}: { children: ReactNode; className?: string; as?: any }) {
  return (
    <Tag
      className={`rounded-xl border border-slate-200 bg-white shadow-sm
                  dark:border-slate-700 dark:bg-slate-900 ${className}`}
    >
      {children}
    </Tag>
  )
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        {children}
      </h2>
      {hint && <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{hint}</p>}
    </div>
  )
}

const ACTION_STYLES: Record<Action, { label: string; className: string }> = {
  answer: {
    label: 'Answered',
    className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20',
  },
  clarify: {
    label: 'Clarifying',
    className: 'bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20',
  },
  escalate: {
    label: 'Escalated',
    className: 'bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/20',
  },
}

export function ActionBadge({ action }: { action: Action }) {
  const style = ACTION_STYLES[action] ?? ACTION_STYLES.answer
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs
                      font-medium ring-1 ring-inset ${style.className}`}>
      {style.label}
    </span>
  )
}

export function ConfidenceBadge({
  level, score,
}: { level?: ConfidenceLevel; score?: number }) {
  if (!level) return null
  const tone =
    level === 'high'
      ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300'
      : level === 'medium'
        ? 'bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300'
        : 'bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300'
  return (
    <span
      title="Combined score from six independent signals, not the model's self-assessment"
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs
                  font-medium ring-1 ring-inset ${tone}`}
    >
      {level} {typeof score === 'number' && `· ${score.toFixed(2)}`}
    </span>
  )
}

/**
 * Citation card. Always shows whether the source is official Village content
 * or placeholder demo data — required by §8, and the difference matters to a
 * resident acting on the answer.
 */
export function SourceCard({ source }: { source: Source }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-slate-800 dark:text-slate-100">
          {source.title}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-slate-400">
          {source.score.toFixed(2)}
        </span>
      </div>
      {source.snippet && (
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          {source.snippet}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {source.is_official ? (
          <span className="rounded bg-village-50 px-1.5 py-0.5 text-[10px] font-semibold
                           uppercase tracking-wide text-village-700
                           dark:bg-village-950 dark:text-village-300">
            Official Village source
          </span>
        ) : (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold
                           uppercase tracking-wide text-amber-900
                           dark:bg-amber-900 dark:text-amber-200">
            Demo data — not official
          </span>
        )}
        {source.url && (
          <span className="text-[10px] text-village-600 dark:text-village-400">
            gardencityny.net ↗
          </span>
        )}
      </div>
    </>
  )

  const className = `block rounded-lg border border-slate-200 bg-slate-50 p-3 text-left
                     transition hover:border-village-300 hover:bg-white
                     dark:border-slate-700 dark:bg-slate-800/60 dark:hover:border-village-600`

  return source.url ? (
    <a href={source.url} target="_blank" rel="noopener noreferrer" className={className}>
      {body}
    </a>
  ) : (
    <div className={className}>{body}</div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center
                    dark:border-slate-700">
      <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{title}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <svg className={`h-4 w-4 animate-spin ${className}`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-80" fill="currentColor"
            d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  )
}

export function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1" aria-label="Assistant is thinking">
      {[0, 1, 2].map((i) => (
        <span key={i}
              className="typing-dot h-1.5 w-1.5 rounded-full bg-current"
              style={{ animationDelay: `${i * 0.18}s` }} />
      ))}
    </span>
  )
}

export function StatTile({
  label, value, sub, tone = 'default',
}: {
  label: string
  value: string | number
  sub?: string
  tone?: 'default' | 'good' | 'warn' | 'bad'
}) {
  const valueTone = {
    default: 'text-slate-900 dark:text-white',
    good: 'text-emerald-600 dark:text-emerald-400',
    warn: 'text-amber-600 dark:text-amber-400',
    bad: 'text-rose-600 dark:text-rose-400',
  }[tone]
  return (
    <Card className="p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${valueTone}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
    </Card>
  )
}

export function ErrorBanner({ message, hint }: { message: string; hint?: string }) {
  return (
    <div role="alert"
         className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm
                    text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">
      <p className="font-medium">{message}</p>
      {hint && <p className="mt-0.5 text-xs opacity-80">{hint}</p>}
    </div>
  )
}
