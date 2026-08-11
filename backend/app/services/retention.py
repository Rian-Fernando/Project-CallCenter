"""Conversation retention and deletion (§17).

PRIVACY POSTURE FOR A MUNICIPAL SYSTEM
    Transcripts of residents' calls are sensitive. This prototype therefore
    defaults to a SHORT retention window (7 days) and stores no caller
    identity at all — no name, phone number, address, or account reference
    exists anywhere in the schema.

    Deletion is real deletion: rows are removed, not flagged. Turns and
    escalations cascade with their conversation.

    Items in the human review queue are retained separately and deliberately.
    They contain the resident's question, which is what an administrator needs
    in order to close a knowledge gap; they are unlinked from the conversation
    when it is purged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db import Conversation, Escalation, Turn, UnansweredQuestion

log = logging.getLogger(__name__)


async def purge_expired(
    db: AsyncSession, *, retention_days: int | None = None,
) -> dict:
    """Delete conversations older than the retention window.

    retention_days = 0 means "keep forever" and performs no deletion.
    """
    days = settings.retention_days if retention_days is None else retention_days
    if days <= 0:
        return {"deleted_conversations": 0, "deleted_turns": 0,
                "retention_days": 0, "cutoff": None}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    ids = [
        row[0] for row in (await db.execute(
            select(Conversation.id).where(Conversation.started_at < cutoff)
        )).all()
    ]
    if not ids:
        return {"deleted_conversations": 0, "deleted_turns": 0,
                "retention_days": days, "cutoff": cutoff}

    turn_count = (await db.execute(
        select(func.count()).select_from(Turn).where(Turn.conversation_id.in_(ids))
    )).scalar_one()

    # Preserve the knowledge gap, drop the link to the conversation.
    await db.execute(
        update(UnansweredQuestion)
        .where(UnansweredQuestion.conversation_id.in_(ids))
        .values(conversation_id=None, transcript=None)
    )

    # Explicit child deletes: SQLite does not enforce ON DELETE CASCADE unless
    # PRAGMA foreign_keys is on for the connection, and relying on that here
    # would leave orphaned rows on some configurations.
    await db.execute(delete(Turn).where(Turn.conversation_id.in_(ids)))
    await db.execute(delete(Escalation).where(Escalation.conversation_id.in_(ids)))
    await db.execute(delete(Conversation).where(Conversation.id.in_(ids)))

    log.info("Retention purge: removed %d conversations older than %d days",
             len(ids), days)
    return {"deleted_conversations": len(ids), "deleted_turns": int(turn_count),
            "retention_days": days, "cutoff": cutoff}


async def delete_conversation(db: AsyncSession, conversation_id: str) -> bool:
    """Delete a single conversation and everything attached to it."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return False

    await db.execute(
        update(UnansweredQuestion)
        .where(UnansweredQuestion.conversation_id == conversation_id)
        .values(conversation_id=None, transcript=None)
    )
    await db.execute(delete(Turn).where(Turn.conversation_id == conversation_id))
    await db.execute(
        delete(Escalation).where(Escalation.conversation_id == conversation_id)
    )
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
    return True


async def delete_all_conversations(db: AsyncSession) -> dict:
    """Purge every conversation. Used by the admin 'delete all' control."""
    total = (await db.execute(select(func.count()).select_from(Conversation))).scalar_one()
    turns = (await db.execute(select(func.count()).select_from(Turn))).scalar_one()

    await db.execute(
        update(UnansweredQuestion).values(conversation_id=None, transcript=None)
    )
    await db.execute(delete(Turn))
    await db.execute(delete(Escalation))
    await db.execute(delete(Conversation))

    log.warning("Admin deleted ALL %d conversations", total)
    return {"deleted_conversations": int(total), "deleted_turns": int(turns),
            "retention_days": settings.retention_days, "cutoff": None}
