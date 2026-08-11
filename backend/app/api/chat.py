"""Chat, RAG search, routing, and department endpoints (§22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.models.schemas import (
    ChatRequest, ChatResponse, ClassifyRequest, ClassifyResponse,
    DepartmentModel, RagSearchRequest, RagSearchResponse,
)
from app.rag.retriever import retriever
from app.routing.departments import get_departments
from app.routing.router import router as intent_router
from app.services.conversation import conversation_service

router = APIRouter(tags=["conversation"])


@router.post("/chat", response_model=ChatResponse,
             summary="Send a resident message and get a grounded reply")
async def chat(request: ChatRequest) -> ChatResponse:
    """Full pipeline: routing, retrieval, confidence assessment, and either an
    answer, a clarifying question, or an escalation.

    Pass the returned `session_id` on subsequent calls to keep conversation
    context (so "when is mine?" resolves against the previous turn).
    """
    session_id = request.session_id or uuid.uuid4().hex
    result = await conversation_service.handle(
        session_id, request.message, channel=request.channel,
    )
    return ChatResponse(**result.as_dict())


@router.post("/rag/search", response_model=RagSearchResponse,
             summary="Search the knowledge base directly")
async def rag_search(request: RagSearchRequest) -> RagSearchResponse:
    """Retrieval without generation — useful for debugging and for the admin
    UI's source browser."""
    result = await retriever.retrieve(
        request.query, department=request.department,
        top_k=request.top_k, min_score=request.min_score,
    )
    return RagSearchResponse(
        query=request.query,
        results=result.sources(limit=request.top_k),
        top_score=round(result.top_score, 4),
        score_margin=round(result.score_margin, 4),
        duration_ms=result.duration_ms,
    )


@router.post("/routing/classify", response_model=ClassifyResponse,
             summary="Classify text into a department")
async def classify(request: ClassifyRequest) -> ClassifyResponse:
    decision = await intent_router.classify(request.text, allow_llm=request.use_llm)
    return ClassifyResponse(**decision.as_dict())


@router.get("/departments", response_model=list[DepartmentModel],
            summary="List Village departments")
async def departments() -> list[DepartmentModel]:
    return [DepartmentModel(**d.as_dict()) for d in get_departments().all()]


@router.get("/knowledge/documents", summary="List indexed source documents")
async def knowledge_documents(
    department: str | None = None,
    official_only: bool = Query(False, description="Exclude DEMO DATA sources."),
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    """Provenance listing: what the assistant actually knows, and where each
    document came from."""
    from sqlalchemy import select

    from app.models.database import session_scope
    from app.models.db import IngestedDocument

    async with session_scope() as db:
        stmt = select(IngestedDocument).order_by(IngestedDocument.title)
        if department:
            stmt = stmt.where(IngestedDocument.department == department)
        if official_only:
            stmt = stmt.where(IngestedDocument.is_official.is_(True))
        rows = (await db.execute(stmt.limit(limit))).scalars().all()

        return {
            "total": len(rows),
            "documents": [
                {
                    "id": r.id,
                    "title": r.title,
                    "department": r.department,
                    "source_type": r.source_type,
                    "source_url": r.source_url,
                    "source_path": r.source_path,
                    "is_official": r.is_official,
                    "chunk_count": r.chunk_count,
                    "char_count": r.char_count,
                    "ingested_at": r.ingested_at.isoformat() if r.ingested_at else None,
                }
                for r in rows
            ],
        }
