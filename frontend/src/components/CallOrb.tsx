/** The call orb: the single visual that tells the caller what the system is doing. */

import { useMemo } from 'react'

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking'

const COPY: Record<OrbState, { label: string; hint: string }> = {
  idle: { label: 'How can I help you?', hint: 'Press start and speak naturally' },
  listening: { label: 'Listening…', hint: 'Pause when you are finished' },
  thinking: { label: 'Looking that up…', hint: 'Searching Village information' },
  speaking: { label: 'Speaking…', hint: 'Say something to interrupt' },
}

export function CallOrb({
  state, level = 0, onClick, disabled,
}: {
  state: OrbState
  level?: number
  onClick: () => void
  disabled?: boolean
}) {
  const copy = COPY[state]

  // Twelve bars mirrored around the centre so the waveform reads as symmetric.
  const bars = useMemo(() => Array.from({ length: 12 }, (_, i) => i), [])

  const ring =
    state === 'listening'
      ? 'from-accent-400 to-accent-600'
      : state === 'thinking'
        ? 'from-village-400 to-village-700'
        : state === 'speaking'
          ? 'from-emerald-400 to-emerald-600'
          : 'from-village-500 to-village-800'

  return (
    <div className="flex flex-col items-center">
      <div className="relative flex h-52 w-52 items-center justify-center">
        {(state === 'listening' || state === 'speaking') && (
          <>
            <span className={`animate-ring absolute h-40 w-40 rounded-full
                              bg-gradient-to-br ${ring} opacity-30`} />
            <span className={`animate-ring absolute h-40 w-40 rounded-full
                              bg-gradient-to-br ${ring} opacity-20`}
                  style={{ animationDelay: '0.8s' }} />
          </>
        )}

        <button
          type="button"
          onClick={onClick}
          disabled={disabled}
          aria-label={
            state === 'idle' ? 'Start call' : 'Stop and end the current turn'
          }
          className={`relative flex h-40 w-40 items-center justify-center rounded-full
                      bg-gradient-to-br ${ring} shadow-xl shadow-village-900/25
                      transition-transform duration-200
                      hover:scale-[1.03] active:scale-[0.98]
                      disabled:cursor-not-allowed disabled:opacity-60
                      ${state !== 'idle' ? 'animate-orb-pulse' : ''}`}
        >
          {state === 'listening' ? (
            <div className="flex h-14 items-center gap-[3px]" aria-hidden>
              {bars.map((i) => {
                const distance = Math.abs(i - 5.5) / 5.5
                const height = 16 + level * 78 * (1 - distance * 0.62)
                return (
                  <span
                    key={i}
                    className="eq-bar w-[3px] rounded-full bg-white/90"
                    style={{
                      height: `${Math.max(8, height)}%`,
                      animationDelay: `${i * 0.06}s`,
                    }}
                  />
                )
              })}
            </div>
          ) : state === 'thinking' ? (
            <svg className="h-12 w-12 animate-spin text-white/90" viewBox="0 0 24 24" fill="none" aria-hidden>
              <circle className="opacity-25" cx="12" cy="12" r="10"
                      stroke="currentColor" strokeWidth="3" />
              <path className="opacity-90" fill="currentColor"
                    d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
            </svg>
          ) : state === 'speaking' ? (
            <svg className="h-14 w-14 text-white" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M3 10v4a1 1 0 001 1h3l4 4V5L7 9H4a1 1 0 00-1 1z" />
              <path d="M16.5 8.5a5 5 0 010 7" stroke="currentColor" strokeWidth="1.8"
                    fill="none" strokeLinecap="round" />
              <path d="M19 6a8.5 8.5 0 010 12" stroke="currentColor" strokeWidth="1.8"
                    fill="none" strokeLinecap="round" />
            </svg>
          ) : (
            <svg className="h-14 w-14 text-white" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M12 15a3 3 0 003-3V6a3 3 0 10-6 0v6a3 3 0 003 3z" />
              <path d="M18 12a6 6 0 01-12 0H4a8 8 0 007 7.94V22h2v-2.06A8 8 0 0020 12h-2z" />
            </svg>
          )}
        </button>
      </div>

      <p aria-live="polite"
         className="mt-5 text-xl font-medium text-slate-800 dark:text-slate-100">
        {copy.label}
      </p>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{copy.hint}</p>
    </div>
  )
}
