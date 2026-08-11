"""Human-in-the-loop knowledge growth (§15).

THE RULE: the AI never writes to its own knowledge base.

Knowledge enters the system through exactly one path, and that path requires a
person to click Approve:

    resident question
      -> AI cannot answer confidently
      -> escalated to a human
      -> question stored in the review queue
      -> administrator writes and approves an answer
      -> answer embedded and indexed
      -> retrievable on the next call

`approve_entry` below is the only runtime code that adds vectors to the store,
and nothing in the conversation pipeline can reach it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import KnowledgeEntry, ReviewStatus, UnansweredQuestion
from app.providers.factory import registry
from app.rag.chunking import chunk_document
from app.rag.loaders import knowledge_entry_to_document
from app.rag.pipeline import _uuid_from_hex

log = logging.getLogger(__name__)


async def index_entry(entry: KnowledgeEntry) -> int:
    """Embed an approved answer and upsert it into the vector store.

    Returns the number of chunks indexed. Raises on failure so the caller can
    report honestly rather than claiming success.
    """
    document = knowledge_entry_to_document(entry)
    chunks = chunk_document(document)
    if not chunks:
        return 0

    store = registry.vector_store
    await store.ensure_collection()
    # Replace any previous version of this entry so editing an answer does not
    # leave the old text retrievable.
    await store.delete_by_doc_id(document.doc_id)

    vectors = await registry.embedding.embed_batch([c.text for c in chunks])
    points = [
        {"id": _uuid_from_hex(c.chunk_id), "vector": v, "payload": c.to_payload()}
        for c, v in zip(chunks, vectors) if v
    ]
    stored = await store.upsert_chunks(points)
    log.info("Indexed knowledge entry '%s' (%d chunks)", entry.question[:60], stored)
    return stored


async def approve_entry(
    db: AsyncSession,
    *,
    question: str,
    answer: str,
    department: str,
    source_title: str | None = None,
    source_url: str | None = None,
    source_document: str | None = None,
    is_official: bool = False,
    approved_by: str = "admin",
    unanswered_id: str | None = None,
) -> tuple[KnowledgeEntry, int, str | None]:
    """Create (or update) an approved answer and index it.

    Returns (entry, chunks_indexed, warning). A non-null warning means the
    answer was saved to the database but could not be indexed — the caller must
    surface that, because an unindexed entry is invisible to retrieval.
    """
    existing = (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.question == question,
            KnowledgeEntry.department == department,
        )
    )).scalars().first()

    if existing:
        entry = existing
        entry.answer = answer
        entry.source_title = source_title
        entry.source_url = source_url
        entry.source_document = source_document
        entry.is_official = is_official
        entry.approved_by = approved_by
        entry.active = True
        entry.updated_at = datetime.now(timezone.utc)
    else:
        entry = KnowledgeEntry(
            question=question, answer=answer, department=department,
            source_title=source_title, source_url=source_url,
            source_document=source_document, is_official=is_official,
            approved_by=approved_by, origin_question_id=unanswered_id,
        )
        db.add(entry)
    await db.flush()

    warning = None
    chunks = 0
    try:
        chunks = await index_entry(entry)
        entry.indexed = chunks > 0
    except Exception as exc:
        # Saving must not be lost because indexing failed; the admin can retry.
        log.error("Indexing approved entry failed: %s", exc)
        entry.indexed = False
        warning = (
            "The answer was saved but could not be added to the search index. "
            "Check that Ollama and the vector store are running, then re-approve."
        )

    if unanswered_id:
        item = await db.get(UnansweredQuestion, unanswered_id)
        if item:
            item.status = ReviewStatus.ANSWERED.value
            item.reviewed_at = datetime.now(timezone.utc)
            item.resulting_entry_id = entry.id

    return entry, chunks, warning


async def deactivate_entry(db: AsyncSession, entry_id: str) -> bool:
    """Retire an approved answer and remove it from the index."""
    entry = await db.get(KnowledgeEntry, entry_id)
    if entry is None:
        return False
    entry.active = False
    entry.indexed = False
    try:
        document = knowledge_entry_to_document(entry)
        await registry.vector_store.delete_by_doc_id(document.doc_id)
    except Exception as exc:
        log.warning("Could not remove entry %s from the index: %s", entry_id, exc)
    return True
