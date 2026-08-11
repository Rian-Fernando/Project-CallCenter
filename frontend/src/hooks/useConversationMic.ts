/**
 * Continuous-call microphone with true barge-in.
 *
 * WHAT WAS WRONG BEFORE
 *   The microphone only opened when the caller pressed a button, and closed
 *   while the assistant spoke. That meant there was nothing to interrupt with —
 *   "barge-in" required a click, which is not how a phone call works.
 *
 * HOW THIS WORKS
 *   The audio stream stays open for the entire call. A single analyser loop
 *   watches the input level and drives a small state machine:
 *
 *     waiting   → silence; nothing is being recorded
 *     recording → speech detected; MediaRecorder is capturing
 *     (silence for `silenceMs`) → emit the utterance, back to waiting
 *
 *   While the assistant is speaking, `setAssistantSpeaking(true)` raises the
 *   detection threshold. The microphone still hears the room, but only genuine,
 *   sustained speech triggers `onBargeIn` — which stops playback instantly.
 *
 * WHY THE HIGHER THRESHOLD
 *   Browser echo cancellation removes most of the assistant's own audio, but
 *   not all of it — especially on laptop speakers at volume. Without a raised
 *   bar the assistant interrupts itself on its own voice. The barge-in also
 *   requires speech to persist for `bargeInMs` rather than firing on a single
 *   loud frame, which rejects coughs, door slams, and keyboard noise.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type MicState = 'off' | 'requesting' | 'waiting' | 'recording' | 'denied' | 'unsupported'

interface Options {
  /** Silence that ends an utterance. */
  silenceMs?: number
  /** RMS above this counts as speech when the assistant is quiet. */
  speechThreshold?: number
  /** Higher bar while the assistant is speaking, to reject its own audio. */
  bargeInThreshold?: number
  /** Speech must persist this long to count as an interruption. */
  bargeInMs?: number
  /** Ignore end-of-speech silence before an utterance is this long. */
  minUtteranceMs?: number
  /** Hard cap so a stuck stream cannot record forever. */
  maxUtteranceMs?: number
  onUtterance?: (blob: Blob) => void
  onBargeIn?: () => void
  onError?: (message: string) => void
}

export function useConversationMic({
  silenceMs = 900,
  speechThreshold = 0.014,
  bargeInThreshold = 0.055,
  bargeInMs = 260,
  minUtteranceMs = 400,
  maxUtteranceMs = 30000,
  onUtterance,
  onBargeIn,
  onError,
}: Options = {}) {
  const [state, setState] = useState<MicState>('off')
  const [level, setLevel] = useState(0)

  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const mimeRef = useRef<string | undefined>(undefined)

  const recordingRef = useRef(false)
  const startedAtRef = useRef(0)
  const lastSoundRef = useRef(0)
  const speechSinceRef = useRef(0)
  const assistantSpeakingRef = useRef(false)
  const activeRef = useRef(false)
  // Suppresses capture entirely (used while a turn is being processed) without
  // tearing the stream down, so the mic doesn't need re-permission each turn.
  const pausedRef = useRef(false)

  // Callbacks in refs so the analyser loop always sees current handlers
  // without being rebuilt on every render.
  const onUtteranceRef = useRef(onUtterance)
  const onBargeInRef = useRef(onBargeIn)
  onUtteranceRef.current = onUtterance
  onBargeInRef.current = onBargeIn

  const stopRecorder = useCallback(() => {
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop()
    }
  }, [])

  const beginRecording = useCallback(() => {
    const stream = streamRef.current
    if (!stream || recordingRef.current) return

    chunksRef.current = []
    let recorder: MediaRecorder
    try {
      recorder = new MediaRecorder(
        stream, mimeRef.current ? { mimeType: mimeRef.current } : undefined,
      )
    } catch {
      return
    }
    recorderRef.current = recorder
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }
    recorder.onstop = () => {
      recordingRef.current = false
      const blob = new Blob(chunksRef.current, {
        type: mimeRef.current ?? 'audio/webm',
      })
      chunksRef.current = []
      setState(activeRef.current ? 'waiting' : 'off')
      // Sub-kilobyte blobs contain no usable speech; sending them would just
      // make Whisper hallucinate on noise.
      if (blob.size > 1500) onUtteranceRef.current?.(blob)
    }

    recordingRef.current = true
    startedAtRef.current = performance.now()
    lastSoundRef.current = performance.now()
    recorder.start(100)
    setState('recording')
  }, [])

  const teardown = useCallback(() => {
    activeRef.current = false
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    if (recorderRef.current?.state === 'recording') {
      try {
        recorderRef.current.stop()
      } catch {
        /* already stopping */
      }
    }
    recorderRef.current = null
    recordingRef.current = false
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => {})
    }
    audioCtxRef.current = null
    setLevel(0)
    setState('off')
  }, [])

  const start = useCallback(async () => {
    if (activeRef.current) return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setState('unsupported')
      onError?.('This browser does not support microphone recording.')
      return
    }

    setState('requesting')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Echo cancellation is what makes an always-open mic viable: it
          // removes most of the assistant's own voice from the input.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
    } catch (error) {
      const name = (error as DOMException)?.name
      setState('denied')
      onError?.(
        name === 'NotAllowedError'
          ? 'Microphone access was blocked. Allow it in your browser settings, then try again.'
          : name === 'NotFoundError'
            ? 'No microphone was found on this device.'
            : 'Could not start the microphone.',
      )
      return
    }

    streamRef.current = stream
    activeRef.current = true
    pausedRef.current = false

    mimeRef.current = [
      'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus',
    ].find((t) => MediaRecorder.isTypeSupported(t))

    const ctx = new AudioContext()
    audioCtxRef.current = ctx
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    analyser.smoothingTimeConstant = 0.6
    ctx.createMediaStreamSource(stream).connect(analyser)
    const buffer = new Float32Array(analyser.fftSize)

    setState('waiting')

    const tick = () => {
      if (!activeRef.current) return
      analyser.getFloatTimeDomainData(buffer)
      let sum = 0
      for (let i = 0; i < buffer.length; i++) sum += buffer[i] * buffer[i]
      const rms = Math.sqrt(sum / buffer.length)
      setLevel(Math.min(1, rms * 9))

      const now = performance.now()
      const assistantSpeaking = assistantSpeakingRef.current
      const threshold = assistantSpeaking ? bargeInThreshold : speechThreshold
      const isSpeech = rms > threshold

      if (isSpeech) {
        if (speechSinceRef.current === 0) speechSinceRef.current = now
        lastSoundRef.current = now
      } else if (!recordingRef.current) {
        speechSinceRef.current = 0
      }

      const sustained = speechSinceRef.current > 0 &&
        now - speechSinceRef.current >= bargeInMs

      if (assistantSpeaking) {
        // Only a sustained burst counts — a single loud frame is noise.
        if (sustained) {
          speechSinceRef.current = 0
          onBargeInRef.current?.()
        }
      } else if (!pausedRef.current) {
        if (!recordingRef.current && isSpeech) {
          beginRecording()
        } else if (recordingRef.current) {
          const elapsed = now - startedAtRef.current
          const quietFor = now - lastSoundRef.current
          if (elapsed > maxUtteranceMs ||
              (elapsed > minUtteranceMs && quietFor > silenceMs)) {
            speechSinceRef.current = 0
            stopRecorder()
          }
        }
      }

      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [
    beginRecording, bargeInMs, bargeInThreshold, maxUtteranceMs, minUtteranceMs,
    onError, silenceMs, speechThreshold, stopRecorder,
  ])

  /** Raise the detection bar while the assistant's voice is playing. */
  const setAssistantSpeaking = useCallback((speaking: boolean) => {
    assistantSpeakingRef.current = speaking
    if (speaking) {
      speechSinceRef.current = 0
      // Discard anything captured before playback started so the assistant's
      // own opening words never become the caller's next "utterance".
      if (recordingRef.current) {
        chunksRef.current = []
        stopRecorder()
      }
    }
  }, [stopRecorder])

  /** Stop capturing without releasing the stream (used while a turn runs). */
  const setPaused = useCallback((paused: boolean) => {
    pausedRef.current = paused
    if (paused && recordingRef.current) {
      chunksRef.current = []
      stopRecorder()
    }
  }, [stopRecorder])

  const stop = useCallback(() => teardown(), [teardown])

  useEffect(() => teardown, [teardown])

  return {
    state,
    level,
    start,
    stop,
    setAssistantSpeaking,
    setPaused,
    isOpen: state === 'waiting' || state === 'recording',
    isRecording: state === 'recording',
  }
}
