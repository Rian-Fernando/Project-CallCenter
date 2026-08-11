/**
 * Sentence-level streaming speech.
 *
 * THE PROBLEM
 *   Waiting for the whole answer before speaking means the caller hears
 *   nothing for 3-6 seconds. On a phone call that reads as a dropped line.
 *
 * THE FIX
 *   Watch the text as it streams in. The moment a complete sentence exists,
 *   send it for synthesis and start playing. Later sentences are synthesized
 *   while the earlier ones are still being spoken, then played in order.
 *
 *   Time to first audio drops from "whole answer" to "first sentence" —
 *   roughly 3-6s down to under 1.5s, with no change to what is actually said.
 *
 * ORDERING
 *   Synthesis requests are issued as soon as a sentence is ready (so they
 *   overlap), but playback strictly follows a queue. Sentence 2 never plays
 *   before sentence 1, even if it finishes synthesizing first.
 *
 * BARGE-IN
 *   `stop()` bumps a generation token. Every in-flight request and queued clip
 *   belonging to an older generation is discarded, so the assistant goes silent
 *   immediately when the caller interrupts.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'

/** Shortest fragment worth synthesizing on its own. */
const MIN_SENTENCE_CHARS = 12

/** Abbreviations whose trailing period does not end a sentence. */
const ABBREVIATIONS = [
  'a.m', 'p.m', 'mr', 'mrs', 'ms', 'dr', 'st', 'ave', 'rd', 'blvd', 'apt',
  'no', 'approx', 'dept', 'inc', 'ltd', 'etc', 'vs', 'e.g', 'i.e', 'jr', 'sr',
]

/**
 * Is the terminator at `index` part of an abbreviation or a number, rather
 * than the end of a sentence?
 *
 * The decisive rule is the last one: real sentences start with a capital, a
 * digit, or end the text. A period followed by a lowercase letter is almost
 * always an abbreviation — which is what "6:00 a.m. on Wednesday" needs, since
 * a lookback list alone never sees "a.m" when standing at the period after "a".
 */
function isAbbreviation(text: string, index: number): boolean {
  // "6.00" or "1.5" — a digit on both sides is a decimal, not a full stop.
  if (index > 0 && /\d/.test(text[index - 1]) && /\d/.test(text[index + 1] ?? '')) {
    return true
  }

  const before = text.slice(Math.max(0, index - 12), index).toLowerCase()
  if (ABBREVIATIONS.some((abbr) => before.endsWith(abbr))) return true

  // A single letter preceded by a space or a period: the "a" of "a.m.",
  // or each letter of "U.S.A."
  if (/(^|[\s.])[a-z]$/i.test(before)) return true

  // What follows decides it. Skip whitespace and look at the next character.
  const rest = text.slice(index + 1)
  const next = rest.match(/\S/)
  if (!next) return false                       // end of text — a real ending
  return /[a-z]/.test(next[0])                  // lowercase ⇒ mid-sentence
}

interface Clip {
  index: number
  blob: Blob | null   // null => speak via the browser fallback
  text: string
}

export function useStreamingSpeech() {
  const [speaking, setSpeaking] = useState(false)
  const [enabled, setEnabled] = useState(true)

  const generationRef = useRef(0)
  const enabledRef = useRef(true)
  enabledRef.current = enabled

  // Text already dispatched for synthesis, so streaming updates don't resend it.
  const consumedRef = useRef(0)
  const nextIndexRef = useRef(0)
  // Ready clips awaiting their turn, keyed by index to preserve order.
  const readyRef = useRef<Map<number, Clip>>(new Map())
  const playingIndexRef = useRef(0)
  const isPlayingRef = useRef(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)

  const releaseAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.onended = null
      audioRef.current.onerror = null
      audioRef.current.pause()
      audioRef.current.src = ''
      audioRef.current = null
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    generationRef.current += 1
    consumedRef.current = 0
    nextIndexRef.current = 0
    playingIndexRef.current = 0
    isPlayingRef.current = false
    readyRef.current.clear()
    releaseAudio()
    window.speechSynthesis?.cancel()
    setSpeaking(false)
  }, [releaseAudio])

  /** Play whatever is next in order, if it has arrived. */
  const pump = useCallback(
    (generation: number) => {
      if (generation !== generationRef.current) return
      if (isPlayingRef.current) return

      const clip = readyRef.current.get(playingIndexRef.current)
      if (!clip) {
        // Next clip isn't synthesized yet; pump() runs again when it lands.
        if (readyRef.current.size === 0) setSpeaking(false)
        return
      }

      readyRef.current.delete(clip.index)
      playingIndexRef.current += 1
      isPlayingRef.current = true
      setSpeaking(true)

      const advance = () => {
        if (generation !== generationRef.current) return
        isPlayingRef.current = false
        releaseAudio()
        pump(generation)
      }

      if (!clip.blob) {
        // Server had no TTS engine — let the browser speak this sentence.
        if (!window.speechSynthesis) return advance()
        const utterance = new SpeechSynthesisUtterance(clip.text)
        utterance.rate = 1.02
        utterance.onend = advance
        utterance.onerror = advance
        window.speechSynthesis.speak(utterance)
        return
      }

      const url = URL.createObjectURL(clip.blob)
      urlRef.current = url
      const audio = new Audio(url)
      audioRef.current = audio
      audio.onended = advance
      audio.onerror = advance
      void audio.play().catch(advance)
    },
    [releaseAudio],
  )

  /** Synthesize one sentence, then try to play. */
  const enqueue = useCallback(
    async (text: string, generation: number) => {
      const index = nextIndexRef.current++
      try {
        const blob = await api.synthesize(text)
        if (generation !== generationRef.current) return
        readyRef.current.set(index, { index, blob, text })
      } catch {
        if (generation !== generationRef.current) return
        // Synthesis failed for this sentence — fall back to browser speech
        // rather than dropping it and leaving a gap in the spoken answer.
        readyRef.current.set(index, { index, blob: null, text })
      }
      pump(generation)
    },
    [pump],
  )

  /**
   * Feed the accumulated answer text so far. Safe to call on every delta —
   * only newly completed sentences are dispatched.
   */
  const push = useCallback(
    (fullText: string) => {
      if (!enabledRef.current) return
      const generation = generationRef.current
      const pending = fullText.slice(consumedRef.current)
      if (!pending) return

      // Find sentence ends, skipping terminators that belong to abbreviations
      // or numbers. Without this, "place containers by 6:00 a.m. on Wednesday"
      // breaks after "a." and "m.", producing fragments too short to speak.
      let searchFrom = 0
      let lastEnd = 0

      while (searchFrom < pending.length) {
        const match = /[.!?]+(?=\s|$)/.exec(pending.slice(searchFrom))
        if (!match) break

        const endIndex = searchFrom + match.index + match[0].length
        const candidate = pending.slice(lastEnd, endIndex)

        if (isAbbreviation(pending, searchFrom + match.index)) {
          searchFrom = endIndex
          continue
        }

        const sentence = candidate.trim()
        // Only advance `consumed` for text we actually dispatch. Advancing past
        // a too-short fragment silently deleted it from the spoken answer,
        // which is how sentences ended up truncated mid-word.
        if (sentence.length >= MIN_SENTENCE_CHARS) {
          void enqueue(sentence, generation)
          consumedRef.current += endIndex - lastEnd
          lastEnd = endIndex
        }
        searchFrom = endIndex
      }
    },
    [enqueue],
  )

  /** Flush any trailing text that never got a terminator. */
  const finish = useCallback(
    (fullText: string) => {
      if (!enabledRef.current) return
      const remainder = fullText.slice(consumedRef.current).trim()
      consumedRef.current = fullText.length
      if (remainder.length >= 3) {
        void enqueue(remainder, generationRef.current)
      }
    },
    [enqueue],
  )

  /** Speak a complete string with no streaming (greetings, refusals). */
  const speak = useCallback(
    (text: string) => {
      const clean = text?.trim()
      if (!clean || !enabledRef.current) return
      stop()
      void enqueue(clean, generationRef.current)
    },
    [enqueue, stop],
  )

  /** Begin a new spoken response; resets sentence tracking. */
  const begin = useCallback(() => {
    stop()
  }, [stop])

  useEffect(() => stop, [stop])

  return { push, finish, speak, begin, stop, speaking, enabled, setEnabled }
}
