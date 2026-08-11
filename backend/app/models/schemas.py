"""Pydantic request/response models (§22).

These are the documented API contract, visible at /docs. Field descriptions are
written for a developer integrating against this service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000,
                         description="What the resident said or typed.")
    session_id: str | None = Field(
        None, max_length=64,
        description="Session id for conversation memory. Omit to start a new call.",
    )
    channel: Literal["browser", "text", "demo", "phone", "sip"] = Field(
        "text", description="Where the input came from. Does not change AI behavior.",
    )


class SourceModel(BaseModel):
    title: str
    url: str = ""
    department: str
    score: float
    is_official: bool = Field(
        ..., description="False means DEMO DATA — must be labeled as such in any UI.",
    )
    source_type: str = ""
    fetched_at: str | None = None
    snippet: str = ""


class EscalationModel(BaseModel):
    id: str
    department: str
    department_name: str
    reason: str
    reason_code: str
    caller_question: str
    conversation_summary: str
    recommended_action: str
    confidence: float
    simulated: bool = True
    transcript: list[dict[str, Any]] = []


class ChatResponse(BaseModel):
    session_id: str
    conversation_id: str
    turn_id: str
    answer: str
    action: Literal["answer", "clarify", "escalate"]
    department: str
    department_name: str
    intent: str
    confidence: float = Field(..., description="Combined score in 0..1.")
    confidence_level: Literal["high", "medium", "low"]
    confidence_signals: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-signal breakdown showing why this score was reached.",
    )
    routing: dict[str, Any] = {}
    sources: list[SourceModel] = []
    escalation: EscalationModel | None = None
    safety_notice: str | None = None
    timings: dict[str, int] = {}
    used_conversation_context: bool = False


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------

class TranscriptionResponse(BaseModel):
    text: str
    language: str = "en"
    duration_ms: int
    audio_seconds: float = 0.0
    confidence: float | None = None


class SynthesisRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str | None = None


class SynthesisInfo(BaseModel):
    """Returned when the server cannot produce audio and the browser should
    speak the text itself using the Web Speech API."""
    client_side_fallback: bool = True
    text: str
    reason: str = "No server-side TTS engine is available."


# --------------------------------------------------------------------------
# RAG / routing
# --------------------------------------------------------------------------

class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    department: str | None = None
    top_k: int = Field(6, ge=1, le=20)
    min_score: float = Field(0.0, ge=0.0, le=1.0)


class RagSearchResponse(BaseModel):
    query: str
    results: list[SourceModel]
    top_score: float
    score_margin: float
    duration_ms: int


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    use_llm: bool = Field(True, description="Allow the LLM fallback for ambiguous input.")


class ClassifyResponse(BaseModel):
    department: str
    department_name: str
    intent: str
    confidence: float
    method: str
    requires_human: bool
    alternatives: list[dict[str, Any]] = []


class DepartmentModel(BaseModel):
    id: str
    name: str
    description: str
    phone: str | None = None
    email: str | None = None
    has_contact_info: bool = False


# --------------------------------------------------------------------------
# Conversations / analytics
# --------------------------------------------------------------------------

class TurnModel(BaseModel):
    id: str
    turn_index: int
    created_at: datetime
    user_text: str
    assistant_text: str
    department: str | None = None
    intent: str | None = None
    confidence_score: float | None = None
    confidence_level: str | None = None
    confidence_signals: dict[str, Any] | None = None
    sources: list[dict[str, Any]] | None = None
    action: str | None = None
    response_ms: int | None = None


class ConversationSummary(BaseModel):
    id: str
    session_id: str
    channel: str
    started_at: datetime
    ended_at: datetime | None = None
    primary_department: str | None = None
    primary_intent: str | None = None
    resolution: str
    escalated: bool
    turn_count: int
    avg_response_ms: float | None = None


class ConversationDetail(ConversationSummary):
    turns: list[TurnModel] = []
    escalations: list[dict[str, Any]] = []


class DepartmentStat(BaseModel):
    department: str
    department_name: str
    count: int
    percentage: float


class IntentStat(BaseModel):
    intent: str
    count: int


class AnalyticsResponse(BaseModel):
    total_conversations: int
    total_turns: int
    ai_resolved: int
    escalated: int
    clarifying: int
    resolution_rate: float = Field(..., description="Share of conversations resolved without a human.")
    avg_response_ms: float | None = None
    p95_response_ms: float | None = None
    unanswered_pending: int
    active_sessions: int
    knowledge_chunks: int
    by_department: list[DepartmentStat] = []
    top_intents: list[IntentStat] = []
    by_confidence: dict[str, int] = {}
    generated_at: datetime


# --------------------------------------------------------------------------
# Unanswered questions / human review (§14, §15)
# --------------------------------------------------------------------------

class UnansweredModel(BaseModel):
    id: str
    created_at: datetime
    last_asked_at: datetime
    question: str
    conversation_id: str | None = None
    detected_department: str | None = None
    attempted_sources: list[dict[str, Any]] | None = None
    confidence_score: float | None = None
    confidence_signals: dict[str, Any] | None = None
    occurrence_count: int
    status: str
    transcript: list[dict[str, Any]] | None = None
    reviewer_note: str | None = None


class ReviewRequest(BaseModel):
    """Claim or annotate an item without publishing an answer."""
    status: Literal["needs_review", "in_review", "dismissed"] = "in_review"
    note: str | None = Field(None, max_length=2000)


class ApproveRequest(BaseModel):
    """Publish a human-written answer into the knowledge base.

    This is the ONLY path by which knowledge enters the system at runtime.
    The AI cannot invoke it.
    """
    question: str = Field(..., min_length=3, max_length=1000)
    answer: str = Field(..., min_length=3, max_length=8000)
    department: str = Field(..., min_length=2, max_length=48)
    source_title: str | None = Field(None, max_length=256)
    source_url: str | None = Field(None, max_length=1024)
    source_document: str | None = Field(None, max_length=512)
    is_official: bool = Field(
        False,
        description="Set true ONLY if verified against an official Village source.",
    )
    approved_by: str = Field("admin", max_length=128)
    unanswered_id: str | None = Field(
        None, description="Marks this review-queue item as answered.",
    )


class KnowledgeEntryModel(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    question: str
    answer: str
    department: str
    source_title: str | None = None
    source_url: str | None = None
    approved_by: str
    is_official: bool
    active: bool
    indexed: bool


# --------------------------------------------------------------------------
# Privacy / admin
# --------------------------------------------------------------------------

class RetentionSettings(BaseModel):
    retention_days: int = Field(..., ge=0, le=3650,
                                description="0 means keep conversations forever.")


class PurgeResult(BaseModel):
    deleted_conversations: int
    deleted_turns: int
    retention_days: int
    cutoff: datetime | None = None


class OperationResult(BaseModel):
    ok: bool = True
    message: str = ""
    details: dict[str, Any] = {}
