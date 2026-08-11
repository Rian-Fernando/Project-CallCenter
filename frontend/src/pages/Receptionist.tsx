/**
 * The resident-facing voice receptionist (§18, §36).
 *
 * CONTINUOUS CALL MODEL
 *   Pressing "Start call" opens the microphone once and keeps it open for the
 *   whole call, exactly like a phone line. There is no push-to-talk:
 *
 *     greeting spoken
 *       → mic listening
 *       → caller speaks, pauses
 *       → utterance transcribed and answered, spoken sentence by sentence
 *       → mic listening again
 *
 *   Speaking while the assistant is talking cuts it off immediately (barge-in)
 *   and the interruption is captured as the next question.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { CallOrb, type OrbState } from '../components/CallOrb'
import { TranscriptPanel } from '../components/TranscriptPanel'
import { ErrorBanner } from '../components/ui'
import { useConversationMic } from '../hooks/useConversationMic'
import { useStreamingSpeech } from '../hooks/useStreamingSpeech'
import { api, streamChat } from '../services/api'
import type { Department, Health, TranscriptLine } from '../services/types'

const uid = () => Math.random().toString(36).slice(2, 10)
const GREETING = 'Thank you for calling the Village of Garden City. How can I help you?'

export default function Receptionist() {
  const [lines, setLines] = useState<TranscriptLine[]>([])
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [inCall, setInCall] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [typed, setTyped] = useState('')

  const speech = useStreamingSpeech()
  const abortRef = useRef<AbortController | null>(null)
  const busyRef = useRef(false)
  const sessionRef = useRef<string | undefined>(undefined)
  sessionRef.current = sessionId

  useEffect(() => {
    api.departments().then(setDepartments).catch(() => {})
    api.health().then(setHealth).catch(() => {})
  }, [])

  const pushLine = useCallback((line: TranscriptLine) => {
    setLines((prev) => [...prev, line])
  }, [])

  // ---- one conversational turn -------------------------------------------
  const runTurn = useCallback(
    async (text: string) => {
      if (!text.trim() || busyRef.current) return
      busyRef.current = true
      setError(null)
      setThinking(true)
      speech.begin()

      pushLine({ id: uid(), role: 'resident', text })
      const replyId = uid()
      pushLine({ id: replyId, role: 'assistant', text: '', pending: true })

      const controller = new AbortController()
      abortRef.current = controller
      let streamed = ''

      const patch = (updates: Partial<TranscriptLine>) =>
        setLines((prev) => prev.map((l) => (l.id === replyId ? { ...l, ...updates } : l)))

      await streamChat(
        text,
        sessionRef.current,
        {
          onMeta: (meta) => {
            if (meta.session_id) setSessionId(meta.session_id)
            patch({
              departmentName: meta.department_name,
              department: meta.department,
              sources: meta.sources ?? [],
            })
          },
          onDelta: (piece) => {
            streamed += piece
            patch({ text: streamed, pending: false })
            // Speak each completed sentence immediately instead of waiting for
            // the full answer — the difference between ~1.5s and ~6s of silence.
            speech.push(streamed)
          },
          onDone: (result) => {
            setSessionId(result.session_id)
            patch({
              text: result.answer || streamed,
              pending: false,
              action: result.action,
              confidence: result.confidence,
              confidenceLevel: result.confidence_level,
              department: result.department,
              departmentName: result.department_name,
              sources: result.sources,
              escalation: result.escalation,
              safetyNotice: result.safety_notice,
              groundingFailed: result.grounding_failed,
              timings: result.timings,
            })
            const finalText = result.answer || streamed
            if (finalText === streamed) {
              // Streaming already spoke most of it; flush the trailing clause
              // so the last sentence is never cut off.
              speech.finish(finalText)
            } else {
              // Verification replaced the draft (refusal or escalation). Say
              // the verified text instead — an unverified draft must never be
              // what the caller is left with.
              speech.speak(finalText)
            }
          },
          onError: (message) => {
            patch({ text: message, pending: false })
            setError({ message })
          },
        },
        controller.signal,
      )

      setThinking(false)
      busyRef.current = false
    },
    [pushLine, speech],
  )

  // ---- microphone --------------------------------------------------------
  const handleUtterance = useCallback(
    async (blob: Blob) => {
      if (busyRef.current) return
      setThinking(true)
      try {
        const { text } = await api.transcribe(blob)
        if (!text.trim()) {
          // VAD removed everything — the mic caught noise, not speech. Stay
          // silent and keep listening rather than nagging the caller.
          setThinking(false)
          return
        }
        await runTurn(text)
      } catch (err: any) {
        setThinking(false)
        setError({ message: err?.message ?? 'Could not process that audio.' })
      }
    },
    [runTurn],
  )

  const handleBargeIn = useCallback(() => {
    // The caller started talking over the assistant. Stop speaking at once and
    // abort the in-flight turn so its remaining sentences never play.
    speech.stop()
    abortRef.current?.abort()
    busyRef.current = false
  }, [speech])

  const mic = useConversationMic({
    onUtterance: handleUtterance,
    onBargeIn: handleBargeIn,
    onError: (message) => setError({ message }),
  })

  // Keep the mic's detection threshold in step with playback so the assistant
  // never interrupts itself on its own voice.
  useEffect(() => {
    mic.setAssistantSpeaking(speech.speaking)
  }, [speech.speaking, mic])

  // Don't capture while a turn is being processed — anything said then would
  // arrive out of order.
  useEffect(() => {
    mic.setPaused(thinking)
  }, [thinking, mic])

  // ---- call lifecycle ----------------------------------------------------
  const startCall = useCallback(async () => {
    setInCall(true)
    setError(null)
    setSessionId(undefined)
    setLines([{ id: uid(), role: 'assistant', text: GREETING }])
    speech.speak(GREETING)
    await mic.start()
  }, [mic, speech])

  const endCall = useCallback(() => {
    abortRef.current?.abort()
    mic.stop()
    speech.stop()
    setInCall(false)
    setThinking(false)
    setSessionId(undefined)
    busyRef.current = false
  }, [mic, speech])

  const orbState: OrbState = speech.speaking
    ? 'speaking'
    : thinking
      ? 'thinking'
      : mic.isRecording
        ? 'listening'
        : inCall
          ? 'listening'
          : 'idle'

  const kbEmpty = health?.services?.vector_store?.meta?.chunks === 0

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      {/* ---------------- Left: the call ---------------- */}
      <section className="flex flex-col items-center justify-start rounded-2xl
                          border border-slate-200 bg-white px-6 py-10 shadow-sm
                          dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-8 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-village-600
                        dark:text-village-400">
            Village of Garden City
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900 dark:text-white">
            AI Receptionist
          </h1>
        </div>

        <CallOrb
          state={orbState}
          level={mic.level}
          onClick={() => (inCall ? endCall() : void startCall())}
          disabled={mic.state === 'requesting'}
        />

        {inCall && (
          <p className="mt-3 text-xs text-slate-400 dark:text-slate-500">
            {speech.speaking
              ? 'Just start talking to interrupt'
              : thinking
                ? 'One moment…'
                : 'Listening — speak whenever you are ready'}
          </p>
        )}

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          {!inCall ? (
            <button
              onClick={() => void startCall()}
              className="rounded-lg bg-village-700 px-8 py-3 text-sm font-semibold
                         text-white shadow-sm transition hover:bg-village-800
                         active:scale-[0.98]"
            >
              Start call
            </button>
          ) : (
            <>
              <button
                onClick={endCall}
                className="rounded-lg bg-rose-600 px-5 py-2.5 text-sm font-semibold
                           text-white transition hover:bg-rose-700"
              >
                End call
              </button>
              {speech.speaking && (
                <button
                  onClick={() => speech.stop()}
                  className="rounded-lg border border-slate-300 px-5 py-2.5 text-sm
                             font-medium text-slate-600 transition hover:bg-slate-50
                             dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  Stop speaking
                </button>
              )}
            </>
          )}
          <button
            onClick={() => speech.setEnabled(!speech.enabled)}
            title={speech.enabled ? 'Mute the assistant' : 'Unmute the assistant'}
            className="rounded-lg border border-slate-300 px-3 py-2.5 text-sm
                       text-slate-600 transition hover:bg-slate-50
                       dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {speech.enabled ? '🔊' : '🔇'}
          </button>
        </div>

        {/* Typing always works — a demo must not depend on mic permission. */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const text = typed.trim()
            if (!text) return
            setTyped('')
            if (!inCall) setInCall(true)
            void runTurn(text)
          }}
          className="mt-6 flex w-full max-w-md gap-2"
        >
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="…or type your question"
            aria-label="Type your question"
            className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                       text-slate-800 placeholder:text-slate-400
                       focus:border-village-500 focus:outline-none
                       dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          />
          <button
            type="submit"
            disabled={!typed.trim() || thinking}
            className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white
                       transition hover:bg-slate-900 disabled:opacity-40
                       dark:bg-slate-700 dark:hover:bg-slate-600"
          >
            Send
          </button>
        </form>

        {error && (
          <div className="mt-4 w-full max-w-md">
            <ErrorBanner message={error.message} hint={error.hint} />
          </div>
        )}

        {kbEmpty && (
          <div className="mt-4 w-full max-w-md rounded-lg border border-amber-300
                          bg-amber-50 p-3 text-xs text-amber-900
                          dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
            The knowledge base is empty. Run <code className="font-mono">./scripts/ingest.sh</code>{' '}
            so the assistant has Village information to cite.
          </div>
        )}

        <div className="mt-8 flex flex-wrap justify-center gap-x-3 gap-y-1 text-xs
                        text-slate-400 dark:text-slate-500">
          {departments.map((d) => (
            <span key={d.id}>{d.name}</span>
          ))}
        </div>
      </section>

      {/* ---------------- Right: transcript ---------------- */}
      <section className="flex h-[640px] flex-col overflow-hidden rounded-2xl border
                          border-slate-200 bg-slate-50 shadow-sm
                          dark:border-slate-700 dark:bg-slate-950">
        <header className="flex items-center justify-between border-b border-slate-200
                           bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
            Live transcript
          </h2>
          {sessionId && (
            <span className="font-mono text-[10px] text-slate-400">
              session {sessionId.slice(0, 8)}
            </span>
          )}
        </header>
        <div className="min-h-0 flex-1">
          <TranscriptPanel lines={lines} />
        </div>
      </section>
    </div>
  )
}
