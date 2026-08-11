/** Shared API types. These mirror the Pydantic models in backend/app/models/schemas.py. */

export type Action = 'answer' | 'clarify' | 'escalate'
export type ConfidenceLevel = 'high' | 'medium' | 'low'

export interface Source {
  title: string
  url: string
  department: string
  score: number
  /** False means DEMO DATA — the UI must label it. */
  is_official: boolean
  source_type: string
  fetched_at: string | null
  snippet: string
}

export interface Escalation {
  id: string
  department: string
  department_name: string
  reason: string
  reason_code: string
  caller_question: string
  conversation_summary: string
  recommended_action: string
  confidence: number
  simulated: boolean
  transcript: Array<Record<string, unknown>>
}

export interface ChatResponse {
  session_id: string
  conversation_id: string
  turn_id: string
  answer: string
  action: Action
  department: string
  department_name: string
  intent: string
  confidence: number
  confidence_level: ConfidenceLevel
  confidence_signals: Record<string, any>
  routing: Record<string, any>
  sources: Source[]
  escalation: Escalation | null
  safety_notice: string | null
  timings: Record<string, number>
  used_conversation_context: boolean
  /** Present on streamed turns: text was spoken before verification failed. */
  grounding_failed?: boolean
  transcript?: string
  no_speech_detected?: boolean
}

export interface Department {
  id: string
  name: string
  description: string
  phone: string | null
  email: string | null
  has_contact_info: boolean
}

export interface DepartmentStat {
  department: string
  department_name: string
  count: number
  percentage: number
}

export interface Analytics {
  total_conversations: number
  total_turns: number
  ai_resolved: number
  escalated: number
  clarifying: number
  resolution_rate: number
  avg_response_ms: number | null
  p95_response_ms: number | null
  unanswered_pending: number
  active_sessions: number
  knowledge_chunks: number
  by_department: DepartmentStat[]
  top_intents: Array<{ intent: string; count: number }>
  by_confidence: Record<string, number>
  generated_at: string
}

export interface Unanswered {
  id: string
  created_at: string
  last_asked_at: string
  question: string
  conversation_id: string | null
  detected_department: string | null
  attempted_sources: Source[] | null
  confidence_score: number | null
  confidence_signals: Record<string, any> | null
  occurrence_count: number
  status: string
  transcript: Array<Record<string, unknown>> | null
  reviewer_note: string | null
}

export interface ConversationSummary {
  id: string
  session_id: string
  channel: string
  started_at: string
  ended_at: string | null
  primary_department: string | null
  primary_intent: string | null
  resolution: string
  escalated: boolean
  turn_count: number
  avg_response_ms: number | null
}

export interface Turn {
  id: string
  turn_index: number
  created_at: string
  user_text: string
  assistant_text: string
  department: string | null
  intent: string | null
  confidence_score: number | null
  confidence_level: string | null
  confidence_signals: Record<string, any> | null
  sources: Source[] | null
  action: string | null
  response_ms: number | null
}

export interface ConversationDetail extends ConversationSummary {
  turns: Turn[]
  escalations: Array<Record<string, any>>
}

export interface ServiceHealth {
  state: 'ok' | 'degraded' | 'unavailable'
  detail: string
  hint: string
  meta: Record<string, any>
}

export interface Health {
  status: 'ok' | 'degraded' | 'unavailable'
  environment: string
  ready_for_calls: boolean
  services: Record<string, ServiceHealth>
  configuration: Record<string, any>
}

/** A line in the live transcript panel. */
export interface TranscriptLine {
  id: string
  role: 'resident' | 'assistant'
  text: string
  pending?: boolean
  action?: Action
  confidence?: number
  confidenceLevel?: ConfidenceLevel
  department?: string
  departmentName?: string
  sources?: Source[]
  escalation?: Escalation | null
  safetyNotice?: string | null
  groundingFailed?: boolean
  timings?: Record<string, number>
}
