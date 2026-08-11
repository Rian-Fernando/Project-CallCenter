"""Ingestion pipeline orchestrator (§7).

    SOURCE -> load -> clean -> chunk -> embed -> Qdrant -> provenance record

Runs the full sequence and records an `IngestedDocument` row for every document
so the admin UI can always answer "where did this answer come from, and is that
source official?".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from app.core.config import settings
from app.models.database import session_scope
from app.models.db import IngestedDocument, KnowledgeEntry
from app.providers.factory import registry
from app.rag.chunking import chunk_documents
from app.rag.documents import SourceDocument
from app.rag.loaders import (
    knowledge_entry_to_document, load_crawled, load_knowledge_dir,
)

log = logging.getLogger(__name__)


@dataclass
class IngestionReport:
    documents: int = 0
    chunks: int = 0
    official_documents: int = 0
    demo_documents: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    by_department: dict[str, int] = field(default_factory=dict)
    by_source_type: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 62,
            "  INGESTION COMPLETE",
            "=" * 62,
            f"  Documents indexed : {self.documents}",
            f"    official        : {self.official_documents}",
            f"    demo/placeholder: {self.demo_documents}",
            f"  Chunks embedded   : {self.chunks}",
            f"  Skipped           : {self.skipped}",
            f"  Duration          : {self.duration_seconds:.1f}s",
            "",
            "  By department:",
        ]
        for dept, n in sorted(self.by_department.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {dept:<18} {n:>4}")
        lines.append("")
        lines.append("  By source type:")
        for st, n in sorted(self.by_source_type.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {st:<18} {n:>4}")
        if self.errors:
            lines.append("")
            lines.append(f"  Errors ({len(self.errors)}):")
            lines.extend(f"    - {e}" for e in self.errors[:10])
        lines.append("=" * 62)
        return "\n".join(lines)


class IngestionPipeline:
    def __init__(self, store=None, embedder=None):
        self.store = store or registry.vector_store
        self.embedder = embedder or registry.embedding

    async def load_documents(self, *, include_web=True, include_files=True,
                             include_approved=True) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        if include_web:
            docs.extend(load_crawled())
        if include_files:
            docs.extend(load_knowledge_dir())
        if include_approved:
            docs.extend(await self._load_approved_entries())

        # A document can arrive from more than one source; keep the first.
        seen: set[str] = set()
        unique: list[SourceDocument] = []
        for doc in docs:
            if doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            unique.append(doc)
        return unique

    async def _load_approved_entries(self) -> list[SourceDocument]:
        try:
            async with session_scope() as session:
                rows = (await session.execute(
                    select(KnowledgeEntry).where(KnowledgeEntry.active.is_(True))
                )).scalars().all()
                return [knowledge_entry_to_document(r) for r in rows]
        except Exception as exc:
            log.warning("Could not load approved knowledge entries: %s", exc)
            return []

    async def run(self, *, recreate: bool = False, **load_kwargs) -> IngestionReport:
        started = time.perf_counter()
        report = IngestionReport()

        docs = await self.load_documents(**load_kwargs)
        if not docs:
            report.errors.append(
                "No documents found. Run the crawler (scripts/crawl.py) or add "
                "files under knowledge/."
            )
            return report

        await self.store.ensure_collection(recreate=recreate)

        chunks = chunk_documents(docs)
        if not chunks:
            report.errors.append("Documents produced no chunks after cleaning.")
            return report

        # --- embed in batches -------------------------------------------
        log.info("Embedding %d chunks with %s...", len(chunks), settings.embedding_model)
        batch_size = 32
        points: list[dict] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            try:
                vectors = await self.embedder.embed_batch([c.text for c in batch])
            except Exception as exc:
                report.errors.append(f"Embedding failed at chunk {start}: {exc}")
                log.error("Embedding batch failed at %d: %s", start, exc)
                continue
            for chunk, vector in zip(batch, vectors):
                if not vector:
                    report.skipped += 1
                    continue
                points.append({
                    "id": _uuid_from_hex(chunk.chunk_id),
                    "vector": vector,
                    "payload": chunk.to_payload(),
                })
            done = min(start + batch_size, len(chunks))
            if done % 128 == 0 or done == len(chunks):
                log.info("  embedded %d/%d", done, len(chunks))

        # --- replace existing chunks for these documents -----------------
        if not recreate:
            for doc in docs:
                try:
                    await self.store.delete_by_doc_id(doc.doc_id)
                except Exception as exc:
                    log.debug("Could not clear old chunks for %s: %s", doc.doc_id, exc)

        stored = await self.store.upsert_chunks(points)
        report.chunks = stored

        # --- provenance records ------------------------------------------
        chunk_counts: dict[str, int] = {}
        for chunk in chunks:
            chunk_counts[chunk.doc_id] = chunk_counts.get(chunk.doc_id, 0) + 1

        async with session_scope() as session:
            if recreate:
                await session.execute(delete(IngestedDocument))
            else:
                ids = [d.doc_id for d in docs]
                await session.execute(
                    delete(IngestedDocument).where(IngestedDocument.id.in_(ids))
                )
            for doc in docs:
                session.add(IngestedDocument(
                    id=doc.doc_id,
                    title=doc.title[:512],
                    department=doc.department,
                    source_type=doc.source_type,
                    source_path=doc.source_path,
                    source_url=doc.source_url,
                    is_official=doc.is_official,
                    content_hash=doc.content_hash,
                    chunk_count=chunk_counts.get(doc.doc_id, 0),
                    char_count=doc.char_count,
                ))

            # Mark approved entries as indexed so the admin UI can show it.
            entry_ids = [d.extra["entry_id"] for d in docs
                         if d.source_type == "admin" and "entry_id" in d.extra]
            if entry_ids:
                for entry in (await session.execute(
                    select(KnowledgeEntry).where(KnowledgeEntry.id.in_(entry_ids))
                )).scalars():
                    entry.indexed = True

        # --- report -------------------------------------------------------
        report.documents = len(docs)
        report.official_documents = sum(1 for d in docs if d.is_official)
        report.demo_documents = report.documents - report.official_documents
        for doc in docs:
            report.by_department[doc.department] = report.by_department.get(doc.department, 0) + 1
            report.by_source_type[doc.source_type] = report.by_source_type.get(doc.source_type, 0) + 1
        report.duration_seconds = time.perf_counter() - started
        return report


def _uuid_from_hex(hex32: str) -> str:
    """Qdrant point ids must be UUIDs or unsigned ints; our chunk ids are
    32-char hex digests, so format them as a UUID string."""
    h = hex32.ljust(32, "0")[:32]
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
