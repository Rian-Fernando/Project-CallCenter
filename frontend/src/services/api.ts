/**
 * API client.
 *
 * Base URL comes from VITE_API_BASE_URL so the frontend can be deployed
 * separately (e.g. Vercel) from the backend. Left blank in development, where
 * the Vite proxy forwards /api to the local FastAPI server.
 */

import type {
  Analytics, ChatResponse, ConversationDetail, ConversationSummary,
  Department, Health, Unanswered,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  code: string
  reference?: string
  hint?: string

  constructor(message: string, code = 'unknown', reference?: string, hint?: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.reference = reference
    this.hint = hint
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData
          ? {}
          : { 'Content-Type': 'application/json' }),
        ...init?.headers,
      },
    })
  } catch {
    // Network-level failure: the server is almost certainly not running.
    throw new ApiError(
      'Could not reach the Garden City service.',
      'network_error',
      undefined,
      'Is the backend running on port 8000?',
    )
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    let code = 'http_error'
    let reference: string | undefined
    try {
      const body = await response.json()
      if (body?.error) {
        message = body.error.message ?? message
        code = body.error.code ?? code
        reference = body.error.reference
      }
    } catch {
      /* non-JSON error body; keep the generic message */
    }
    throw new ApiError(message, code, reference)
  }

  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  departments: () => request<Department[]>('/api/departments'),

  chat: (message: string, sessionId?: string, channel = 'text') =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, session_id: sessionId, channel }),
    }),

  analytics: (days = 30) => request<Analytics>(`/api/analytics?days=${days}`),

  conversations: (limit = 50) =>
    request<ConversationSummary[]>(`/api/conversations?limit=${limit}`),

  conversation: (id: string) =>
    request<ConversationDetail>(`/api/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/conversations/${id}`, {
      method: 'DELETE',
    }),

  deleteAllConversations: () =>
    request<Record<string, unknown>>('/api/conversations?confirm=true', {
      method: 'DELETE',
    }),

  unanswered: (status?: string) =>
    request<Unanswered[]>(
      `/api/unanswered${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),

  reviewQuestion: (id: string, status: string, note?: string) =>
    request<{ ok: boolean; message: string }>(`/api/knowledge/review/${id}`, {
      method: 'POST',
      body: JSON.stringify({ status, note }),
    }),

  approveKnowledge: (payload: {
    question: string
    answer: string
    department: string
    source_title?: string | null
    source_url?: string | null
    is_official?: boolean
    unanswered_id?: string | null
  }) =>
    request<{ ok: boolean; message: string; details: Record<string, unknown> }>(
      '/api/knowledge/approve',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  knowledgeEntries: () =>
    request<Array<Record<string, any>>>('/api/knowledge/entries'),

  knowledgeDocuments: () =>
    request<{ total: number; documents: Array<Record<string, any>> }>(
      '/api/knowledge/documents',
    ),

  escalations: () =>
    request<{ total: number; escalations: Array<Record<string, any>> }>(
      '/api/escalations',
    ),

  privacySettings: () => request<Record<string, any>>('/api/privacy/settings'),

  setRetention: (days: number) =>
    request<{ ok: boolean; message: string }>(
      `/api/privacy/retention?days=${days}`,
      { method: 'POST' },
    ),

  purge: () =>
    request<Record<string, unknown>>('/api/privacy/purge', { method: 'POST' }),

  gogovStatus: () => request<Record<string, any>>('/api/gogov/status'),

  /** Transcribe recorded audio. */
  async transcribe(blob: Blob): Promise<{ text: string; duration_ms: number }> {
    const form = new FormData()
    form.append('audio', blob, 'speech.webm')
    return request('/api/voice/transcribe', { method: 'POST', body: form })
  },

  /**
   * Synthesize speech.
   *
   * Returns null when no server-side TTS engine is available, signalling the
   * caller to fall back to the browser's Web Speech API.
   */
  async synthesize(text: string): Promise<Blob | null> {
    const response = await fetch(`${BASE}/api/voice/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!response.ok) throw new ApiError('Speech synthesis failed', 'tts_failed')
    if (response.headers.get('content-type')?.includes('application/json')) {
      return null
    }
    return response.blob()
  },
}

/** Server-Sent Events for a streaming turn. */
export interface StreamHandlers {
  onMeta?: (data: any) => void
  onDelta?: (text: string) => void
  onDone?: (data: ChatResponse) => void
  onError?: (message: string) => void
}

export async function streamChat(
  message: string,
  sessionId: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${BASE}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId, channel: 'browser' }),
      signal,
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') return
    handlers.onError?.('Could not reach the Garden City service.')
    return
  }

  if (!response.ok || !response.body) {
    handlers.onError?.('The service could not start a response.')
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line.
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''

      for (const frame of frames) {
        let event = 'message'
        let payload = ''
        for (const line of frame.split('\n')) {
          if (line.startsWith('event: ')) event = line.slice(7).trim()
          else if (line.startsWith('data: ')) payload += line.slice(6)
        }
        if (!payload) continue

        let data: any
        try {
          data = JSON.parse(payload)
        } catch {
          continue
        }

        if (event === 'meta') handlers.onMeta?.(data)
        else if (event === 'delta') handlers.onDelta?.(data.text ?? '')
        else if (event === 'done') handlers.onDone?.(data as ChatResponse)
        else if (event === 'error') handlers.onError?.(data.message ?? 'Something went wrong.')
      }
    }
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      handlers.onError?.('The connection was interrupted.')
    }
  }
}
