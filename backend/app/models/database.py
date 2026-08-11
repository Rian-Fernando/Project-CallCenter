"""Async database engine and session management.

The same code path serves SQLite (prototype default) and PostgreSQL
(production). Only DATABASE_URL changes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

from app.core.config import settings
from app.models.db import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = settings.resolved_database_url
        if url.startswith("sqlite"):
            # Make sure the parent directory exists before SQLite tries to
            # create the file, otherwise it fails with a cryptic OperationalError.
            db_path = Path(url.split("///", 1)[1])
            db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(url, echo=False, future=True, pool_pre_ping=True)
        log.info("Database engine created (%s)", url.split("://", 1)[0])
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False,
        )
    return _sessionmaker


async def init_db() -> None:
    """Create tables if they don't exist.

    Deliberately create_all rather than Alembic: this is a prototype with a
    disposable database. PRODUCTION_ROADMAP.md covers adding real migrations.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            # WAL lets the retention job write while a call is being served.
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database schema ready")


async def close_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine, _sessionmaker = None, None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Standalone context manager for scripts and background jobs."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
