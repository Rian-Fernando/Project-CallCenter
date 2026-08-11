"""Admin endpoints — analytics, conversations, review queue, privacy (§13–17).

NO AUTHENTICATION. This is a local prototype and every route here is open.
That is acceptable only because the service binds to 127.0.0.1 and holds no
real resident data. Adding authentication and audit logging is the first item
in SECURITY_ROADMAP.md and is required before any real deployment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.models.database import get_session
from app.models.db import (
    Conversation, Escalation, KnowledgeEntry, ReviewStatus, Turn,
    UnansweredQuestion,
)
from app.models.schemas import (
    AnalyticsResponse, ApproveRequest, ConversationDetail, ConversationSummary,
    DepartmentStat, IntentStat, KnowledgeEntryModel, OperationResult,
    PurgeResult, ReviewRequest, UnansweredModel,
)
from app.providers.factory import registry
from app.routing.departments import get_departments
from app.services import retention as retention_service
from app.services.knowledge_admin import approve_entry, deactivate_entry
from app.services.memory import session_store

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


# ----------------------------------------------------------------------
# Analytics (§13) — every number below is computed from the database.
# ----------------------------------------------------------------------

@router.get("/analytics", response_model=AnalyticsResponse,
            summary="Dashboard metrics computed from stored conversations")
async def analytics(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days."),
    db: AsyncSession = Depends(get_session),
) -> AnalyticsResponse:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    departments = get_departments()

    total = (await db.execute(
        select(func.count()).select_from(Conversation)
        .where(Conversation.started_at >= since)
    )).scalar_one()

    turns_total = (await db.execute(
        select(func.count()).select_from(Turn).where(Turn.created_at >= since)
    )).scalar_one()

    by_resolution = dict((await db.execute(
        select(Conversation.resolution, func.count())
        .where(Conversation.started_at >= since)
        .group_by(Conversation.resolution)
    )).all())

    escalated = (await db.execute(
        select(func.count()).select_from(Conversation)
        .where(Conversation.started_at >= since, Conversation.escalated.is_(True))
    )).scalar_one()

    ai_resolved = by_resolution.get("ai_resolved", 0)
    clarifying = by_resolution.get("clarifying", 0)

    # Timings come from turns, not conversations: a conversation's average
    # hides the spread, and p95 is what reveals a bad experience.
    durations = sorted(
        row[0] for row in (await db.execute(
            select(Turn.response_ms)
            .where(Turn.created_at >= since, Turn.response_ms.isnot(None))
        )).all() if row[0]
    )
    avg_ms = sum(durations) / len(durations) if durations else None
    p95_ms = durations[min(len(durations) - 1, int(len(durations) * 0.95))] \
        if durations else None

    dept_rows = (await db.execute(
        select(Turn.department, func.count())
        .where(Turn.created_at >= since, Turn.department.isnot(None))
        .group_by(Turn.department).order_by(func.count().desc())
    )).all()
    dept_total = sum(n for _, n in dept_rows) or 1
    by_department = [
        DepartmentStat(
            department=d, department_name=departments.name_of(d),
            count=n, percentage=round(100 * n / dept_total, 1),
        )
        for d, n in dept_rows
    ]

    intent_rows = (await db.execute(
        select(Turn.intent, func.count())
        .where(Turn.created_at >= since, Turn.intent.isnot(None))
        .group_by(Turn.intent).order_by(func.count().desc()).limit(10)
    )).all()

    confidence_rows = (await db.execute(
        select(Turn.confidence_level, func.count())
        .where(Turn.created_at >= since, Turn.confidence_level.isnot(None))
        .group_by(Turn.confidence_level)
    )).all()

    pending = (await db.execute(
        select(func.count()).select_from(UnansweredQuestion)
        .where(UnansweredQuestion.status.in_(
            [ReviewStatus.NEEDS_REVIEW.value, ReviewStatus.IN_REVIEW.value]))
    )).scalar_one()

    return AnalyticsResponse(
        total_conversations=int(total),
        total_turns=int(turns_total),
        ai_resolved=int(ai_resolved),
        escalated=int(escalated),
        clarifying=int(clarifying),
        resolution_rate=round(ai_resolved / total, 3) if total else 0.0,
        avg_response_ms=round(avg_ms, 1) if avg_ms else None,
        p95_response_ms=float(p95_ms) if p95_ms else None,
        unanswered_pending=int(pending),
        active_sessions=session_store.active_count,
        knowledge_chunks=await registry.vector_store.count(),
        by_department=by_department,
        top_intents=[IntentStat(intent=i, count=n) for i, n in intent_rows],
        by_confidence={k: int(v) for k, v in confidence_rows},
        generated_at=datetime.now(timezone.utc),
    )


# ----------------------------------------------------------------------
# Conversations (§16)
# ----------------------------------------------------------------------

@router.get("/conversations", response_model=list[ConversationSummary],
            summary="List logged conversations")
async def list_conversations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    escalated_only: bool = False,
    db: AsyncSession = Depends(get_session),
) -> list[ConversationSummary]:
    stmt = select(Conversation).order_by(Conversation.started_at.desc())
    if department:
        stmt = stmt.where(Conversation.primary_department == department)
    if escalated_only:
        stmt = stmt.where(Conversation.escalated.is_(True))
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return [ConversationSummary.model_validate(r, from_attributes=True) for r in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail,
            summary="Full transcript with decision trace")
async def get_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFoundError(f"Conversation {conversation_id} not found.")

    escalations = (await db.execute(
        select(Escalation).where(Escalation.conversation_id == conversation_id)
    )).scalars().all()

    detail = ConversationDetail.model_validate(conversation, from_attributes=True)
    detail.escalations = [
        {
            "id": e.id, "department": e.department, "reason": e.reason,
            "reason_code": e.reason_code, "caller_question": e.caller_question,
            "conversation_summary": e.conversation_summary,
            "recommended_action": e.recommended_action,
            "confidence_score": e.confidence_score, "simulated": e.simulated,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in escalations
    ]
    return detail


@router.delete("/conversations/{conversation_id}", response_model=OperationResult,
               summary="Delete one conversation and its transcript")
async def delete_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_session),
) -> OperationResult:
    deleted = await retention_service.delete_conversation(db, conversation_id)
    if not deleted:
        raise NotFoundError(f"Conversation {conversation_id} not found.")
    await db.commit()
    return OperationResult(ok=True, message="Conversation permanently deleted.")


@router.delete("/conversations", response_model=PurgeResult,
               summary="Delete ALL conversations")
async def delete_all_conversations(
    confirm: bool = Query(False, description="Must be true. Guards against accidents."),
    db: AsyncSession = Depends(get_session),
) -> PurgeResult:
    if not confirm:
        raise ValidationError(
            "Refusing to delete everything without confirm=true.",
            user_message="Confirmation is required to delete all conversations.",
        )
    result = await retention_service.delete_all_conversations(db)
    await db.commit()
    return PurgeResult(**result)


# ----------------------------------------------------------------------
# Escalations (§11)
# ----------------------------------------------------------------------

@router.get("/escalations", summary="List simulated department transfers")
async def list_escalations(
    limit: int = Query(50, ge=1, le=500),
    department: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Escalation).order_by(Escalation.created_at.desc())
    if department:
        stmt = stmt.where(Escalation.department == department)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    departments = get_departments()
    return {
        "total": len(rows),
        "escalations": [
            {
                "id": e.id, "created_at": e.created_at.isoformat(),
                "department": e.department,
                "department_name": departments.name_of(e.department),
                "reason": e.reason, "reason_code": e.reason_code,
                "caller_question": e.caller_question,
                "conversation_summary": e.conversation_summary,
                "recommended_action": e.recommended_action,
                "confidence_score": e.confidence_score,
                "conversation_id": e.conversation_id,
                "simulated": e.simulated,
            }
            for e in rows
        ],
    }


# ----------------------------------------------------------------------
# Unanswered questions and human review (§14, §15)
# ----------------------------------------------------------------------

@router.get("/unanswered", response_model=list[UnansweredModel],
            summary="Questions the AI could not answer")
async def list_unanswered(
    status: str | None = Query(None, description="needs_review | in_review | answered | dismissed"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
) -> list[UnansweredModel]:
    stmt = select(UnansweredQuestion).order_by(
        UnansweredQuestion.occurrence_count.desc(),
        UnansweredQuestion.last_asked_at.desc(),
    )
    if status:
        stmt = stmt.where(UnansweredQuestion.status == status)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return [UnansweredModel.model_validate(r, from_attributes=True) for r in rows]


@router.post("/knowledge/review/{question_id}", response_model=OperationResult,
             summary="Claim or annotate a review-queue item")
async def review_question(
    question_id: str, request: ReviewRequest,
    db: AsyncSession = Depends(get_session),
) -> OperationResult:
    item = await db.get(UnansweredQuestion, question_id)
    if item is None:
        raise NotFoundError("That question is not in the review queue.")
    item.status = request.status
    item.reviewer_note = request.note
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return OperationResult(ok=True, message=f"Marked as {request.status}.")


@router.post("/knowledge/approve", response_model=OperationResult,
             summary="Approve a human-written answer into the knowledge base")
async def approve_knowledge(
    request: ApproveRequest, db: AsyncSession = Depends(get_session),
) -> OperationResult:
    """The ONLY runtime path that adds knowledge (§15).

    Requires an explicit human action. The AI cannot call this.
    """
    if not get_departments().exists(request.department):
        raise ValidationError(
            f"Unknown department '{request.department}'.",
            user_message="Please choose a valid department.",
        )

    entry, chunks, warning = await approve_entry(
        db,
        question=request.question, answer=request.answer,
        department=request.department, source_title=request.source_title,
        source_url=request.source_url, source_document=request.source_document,
        is_official=request.is_official, approved_by=request.approved_by,
        unanswered_id=request.unanswered_id,
    )
    await db.commit()

    return OperationResult(
        ok=warning is None,
        message=warning or (
            f"Approved and indexed. This answer is now searchable "
            f"({chunks} chunk{'s' if chunks != 1 else ''})."
        ),
        details={"entry_id": entry.id, "chunks_indexed": chunks,
                 "indexed": entry.indexed},
    )


@router.get("/knowledge/entries", response_model=list[KnowledgeEntryModel],
            summary="List admin-approved answers")
async def list_knowledge_entries(
    active_only: bool = True, limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
) -> list[KnowledgeEntryModel]:
    stmt = select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
    if active_only:
        stmt = stmt.where(KnowledgeEntry.active.is_(True))
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return [KnowledgeEntryModel.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/knowledge/entries/{entry_id}", response_model=OperationResult,
               summary="Retire an approved answer")
async def retire_entry(
    entry_id: str, db: AsyncSession = Depends(get_session),
) -> OperationResult:
    if not await deactivate_entry(db, entry_id):
        raise NotFoundError("That knowledge entry does not exist.")
    await db.commit()
    return OperationResult(ok=True, message="Entry retired and removed from search.")


# ----------------------------------------------------------------------
# Privacy / retention (§17)
# ----------------------------------------------------------------------

@router.get("/privacy/settings", summary="Current retention configuration")
async def privacy_settings() -> dict:
    return {
        "retention_days": settings.retention_days,
        "store_audio": settings.store_audio,
        "options": [7, 30, 90, 0],
        "option_labels": {"7": "7 days", "30": "30 days", "90": "90 days",
                          "0": "Never delete"},
        "transcript_location": settings.resolved_database_url.split("://", 1)[0],
        "stores_personal_information": False,
        "notes": [
            "No caller name, phone number, address, or account reference is "
            "stored anywhere in the schema.",
            "Session identifiers are random per call and are not linked to a person.",
            "Audio is processed in memory and never written to disk when "
            "STORE_AUDIO is false.",
            "Retention changes made here apply to this process only. Set "
            "RETENTION_DAYS in .env to make them permanent.",
        ],
    }


@router.post("/privacy/purge", response_model=PurgeResult,
             summary="Delete conversations past the retention window")
async def purge(
    retention_days: int | None = Query(None, ge=0, le=3650),
    db: AsyncSession = Depends(get_session),
) -> PurgeResult:
    result = await retention_service.purge_expired(db, retention_days=retention_days)
    await db.commit()
    return PurgeResult(**result)


@router.post("/privacy/retention", response_model=OperationResult,
             summary="Change the retention window for this process")
async def set_retention(days: int = Query(..., ge=0, le=3650)) -> OperationResult:
    settings.retention_days = days
    label = "never delete" if days == 0 else f"{days} days"
    return OperationResult(
        ok=True,
        message=f"Retention set to {label} for this process. "
                f"Set RETENTION_DAYS={days} in .env to persist it.",
        details={"retention_days": days},
    )


# ----------------------------------------------------------------------
# GoGov (§21)
# ----------------------------------------------------------------------

@router.get("/gogov/status", summary="Which GoGov implementation is active")
async def gogov_status() -> dict:
    from app.integrations.gogov import get_gogov_service
    service = get_gogov_service()
    return {
        "mode": service.mode,
        "is_live": service.is_live,
        "badge": "LIVE GOV SERVICE" if service.is_live else "MOCK GOV SERVICE",
        "note": ("GoGov publishes no public API specification. The live "
                 "implementation is intentionally a stub — see "
                 "docs/GOGOV_INTEGRATION.md."),
    }


@router.post("/gogov/request", summary="Create a (simulated) service request")
async def gogov_request(
    department: str, summary: str, details: str = "",
) -> dict:
    from app.integrations.gogov import get_gogov_service
    return await get_gogov_service().create_request(
        department=department, summary=summary, details=details,
    )
