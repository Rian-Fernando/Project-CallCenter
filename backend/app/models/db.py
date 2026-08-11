"""SQLAlchemy 2.0 models — conversation logging, review queue, knowledge entries.

PRIVACY NOTE (§17): these tables deliberately store no caller identity. There is
no name, phone number, address, or account column anywhere in this schema. A
session id is a random per-call UUID that is never linked to a person and is
purged by the retention job (default: 7 days).

PORTABILITY: only portable column types are used (String/Text/Float/Integer/
Boolean/DateTime/JSON), so the same models run unchanged on SQLite and
PostgreSQL. Switching is a DATABASE_URL change.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Enumerations (stored as plain strings for cross-database portability)
# --------------------------------------------------------------------------

class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Resolution(str, enum.Enum):
    AI_RESOLVED = "ai_resolved"          # answered confidently from the KB
    CLARIFYING = "clarifying"            # asked the resident a follow-up
    ESCALATED = "escalated"              # handed to a human department
    ABANDONED = "abandoned"              # resident left mid-conversation
    ERROR = "error"                      # a service failed


class ReviewStatus(str, enum.Enum):
    NEEDS_REVIEW = "needs_review"
    IN_REVIEW = "in_review"
    ANSWERED = "answered"                # admin wrote an answer -> in KB
    DISMISSED = "dismissed"              # not a real knowledge gap


class ChannelType(str, enum.Enum):
    """Where the audio/text came from. The AI backend is identical for all of
    these — see providers/audio_ingress.py (§19)."""
    BROWSER = "browser"
    TEXT = "text"
    DEMO = "demo"
    PHONE = "phone"                      # future: SIP/Twilio
    SIP = "sip"


# --------------------------------------------------------------------------
# Core tables
# --------------------------------------------------------------------------

class Conversation(Base):
    """One call/session. Holds no personally identifying information."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(16), default=ChannelType.BROWSER.value)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Rolled up from the turns, for fast dashboard queries.
    primary_department: Mapped[str | None] = mapped_column(String(48), index=True, default=None)
    primary_intent: Mapped[str | None] = mapped_column(String(96), default=None)
    resolution: Mapped[str] = mapped_column(String(24), default=Resolution.AI_RESOLVED.value, index=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_ms: Mapped[float | None] = mapped_column(Float, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="Turn.turn_index", lazy="selectin",
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (Index("ix_conv_started_dept", "started_at", "primary_department"),)


class Turn(Base):
    """A single resident question + AI response, with full decision trace.

    Storing the trace (not just the answer) is what makes the system
    explainable: an administrator can see exactly which documents were
    retrieved and which signal caused an escalation.
    """

    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user_text: Mapped[str] = mapped_column(Text)
    assistant_text: Mapped[str] = mapped_column(Text, default="")

    # Routing
    department: Mapped[str | None] = mapped_column(String(48), index=True, default=None)
    intent: Mapped[str | None] = mapped_column(String(96), default=None)
    routing_method: Mapped[str | None] = mapped_column(String(24), default=None)  # rules | llm | hybrid
    routing_confidence: Mapped[float | None] = mapped_column(Float, default=None)

    # Confidence engine
    confidence_score: Mapped[float | None] = mapped_column(Float, default=None)
    confidence_level: Mapped[str | None] = mapped_column(String(12), index=True, default=None)
    confidence_signals: Mapped[dict | None] = mapped_column(JSON, default=None)
    policy_restriction: Mapped[str | None] = mapped_column(String(48), default=None)

    # Retrieval: [{title, url, department, score, snippet, doc_id}, ...]
    sources: Mapped[list | None] = mapped_column(JSON, default=None)
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0)

    # Performance breakdown, in milliseconds
    response_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    stt_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    tts_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    action: Mapped[str | None] = mapped_column(String(24), default=None)  # answer|clarify|escalate

    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class Escalation(Base):
    """A simulated transfer to a human department (§11)."""

    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    department: Mapped[str] = mapped_column(String(48), index=True)
    reason: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(String(48), default="low_confidence")
    caller_question: Mapped[str] = mapped_column(Text)
    conversation_summary: Mapped[str] = mapped_column(Text, default="")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    confidence_score: Mapped[float | None] = mapped_column(Float, default=None)

    # Simulated for the prototype; maps to a real SIP transfer later.
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    conversation: Mapped[Conversation] = relationship(back_populates="escalations")


class UnansweredQuestion(Base):
    """A question the AI could not confidently answer (§14).

    This is the input queue for human-in-the-loop knowledge growth (§15).
    """

    __tablename__ = "unanswered_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    question: Mapped[str] = mapped_column(Text)
    normalized_question: Mapped[str] = mapped_column(Text, index=True)  # for dedupe
    conversation_id: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    transcript: Mapped[list | None] = mapped_column(JSON, default=None)

    detected_department: Mapped[str | None] = mapped_column(String(48), index=True, default=None)
    attempted_sources: Mapped[list | None] = mapped_column(JSON, default=None)
    confidence_score: Mapped[float | None] = mapped_column(Float, default=None)
    confidence_signals: Mapped[dict | None] = mapped_column(JSON, default=None)
    outcome: Mapped[str] = mapped_column(String(24), default=Resolution.ESCALATED.value)

    # How many times residents have asked essentially this question.
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, index=True)
    last_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    status: Mapped[str] = mapped_column(
        String(24), default=ReviewStatus.NEEDS_REVIEW.value, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reviewer_note: Mapped[str | None] = mapped_column(Text, default=None)
    resulting_entry_id: Mapped[str | None] = mapped_column(String(32), default=None)


class KnowledgeEntry(Base):
    """An admin-authored answer promoted into the knowledge base (§15).

    The AI can never write to this table. Entries are created only through
    POST /api/knowledge/approve, which requires an explicit human action.
    """

    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    department: Mapped[str] = mapped_column(String(48), index=True)

    source_title: Mapped[str | None] = mapped_column(String(256), default=None)
    source_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    source_document: Mapped[str | None] = mapped_column(String(512), default=None)

    approved_by: Mapped[str] = mapped_column(String(128), default="admin")
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    origin_question_id: Mapped[str | None] = mapped_column(String(32), default=None)


class IngestedDocument(Base):
    """Provenance for every document in the vector store.

    Lets the admin UI answer "where did this answer come from, and is that
    source official Village content or demo placeholder data?"
    """

    __tablename__ = "ingested_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    title: Mapped[str] = mapped_column(String(512))
    department: Mapped[str] = mapped_column(String(48), index=True)
    source_type: Mapped[str] = mapped_column(String(24))       # web | pdf | markdown | txt | faq | admin
    source_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    source_url: Mapped[str | None] = mapped_column(String(1024), default=None)

    # False => DEMO DATA, must be labeled as such everywhere it appears.
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
