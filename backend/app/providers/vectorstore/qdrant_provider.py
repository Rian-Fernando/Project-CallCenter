"""Qdrant vector store provider.

TWO MODES, one code path:

  Embedded (default)  QDRANT_URL blank -> qdrant-client runs Qdrant in-process
                      against ./data/qdrant. Real Qdrant, real HNSW index, no
                      server, no Docker, no ports. Chosen as the default because
                      it makes first-run setup zero-step.

  Server              QDRANT_URL set    -> normal client/server against a Qdrant
                      instance (see docker/docker-compose.yml). Use this when
                      more than one process needs the index at once, or in
                      production.

The embedded mode holds an exclusive file lock, so only one process may open it
at a time. That is why ingestion prefers to go through the running API (see
scripts/ingest.py) rather than opening the store a second time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models as qmodels

from app.core.config import settings
from app.core.errors import KnowledgeBaseError
from app.providers.base import (
    EmbeddingProvider, HealthState, HealthStatus, RetrievedChunk, VectorStoreProvider,
)

log = logging.getLogger(__name__)


class QdrantProvider(VectorStoreProvider):
    def __init__(self, embedder: EmbeddingProvider, collection: str | None = None):
        self.embedder = embedder
        self.collection = collection or settings.qdrant_collection
        self._client: AsyncQdrantClient | None = None
        self._lock = asyncio.Lock()

    # -- connection --------------------------------------------------------
    async def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    if settings.uses_embedded_qdrant:
                        path = settings.qdrant_storage_path
                        path.mkdir(parents=True, exist_ok=True)
                        self._client = AsyncQdrantClient(path=str(path))
                        log.info("Qdrant embedded mode at %s", path)
                    else:
                        self._client = AsyncQdrantClient(url=settings.qdrant_url)
                        log.info("Qdrant server mode at %s", settings.qdrant_url)
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                log.debug("Qdrant close failed", exc_info=True)
            self._client = None

    async def ensure_collection(self, *, recreate: bool = False) -> None:
        client = await self._get_client()
        exists = await client.collection_exists(self.collection)
        if exists and recreate:
            await client.delete_collection(self.collection)
            exists = False
        if not exists:
            await client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.embedder.dimension, distance=qmodels.Distance.COSINE,
                ),
            )
            log.info("Created collection '%s' (%d dims)",
                     self.collection, self.embedder.dimension)

    # -- health ------------------------------------------------------------
    async def health(self) -> HealthStatus:
        mode = "embedded" if settings.uses_embedded_qdrant else "server"
        try:
            client = await self._get_client()
            if not await client.collection_exists(self.collection):
                return HealthStatus(
                    name="vector_store", state=HealthState.DEGRADED,
                    detail=f"Collection '{self.collection}' does not exist yet.",
                    hint="Run:  ./scripts/ingest.sh",
                    meta={"mode": mode, "chunks": 0},
                )
            n = (await client.count(self.collection, exact=True)).count
            if n == 0:
                return HealthStatus(
                    name="vector_store", state=HealthState.DEGRADED,
                    detail="Knowledge base is empty — the AI has nothing to cite.",
                    hint="Run:  ./scripts/ingest.sh",
                    meta={"mode": mode, "chunks": 0},
                )
            return HealthStatus(
                name="vector_store", state=HealthState.OK,
                detail=f"{n} chunks indexed ({mode} mode)",
                meta={"mode": mode, "chunks": n, "collection": self.collection},
            )
        except Exception as exc:
            hint = ("Another process may hold the embedded Qdrant lock — stop it "
                    "and retry." if settings.uses_embedded_qdrant
                    else f"Is Qdrant reachable at {settings.qdrant_url}?")
            return HealthStatus(
                name="vector_store", state=HealthState.UNAVAILABLE,
                detail=f"Qdrant unavailable: {exc}", hint=hint, meta={"mode": mode},
            )

    # -- search ------------------------------------------------------------
    async def search(
        self, query: str, *, top_k: int = 6, department: str | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        client = await self._get_client()
        try:
            if not await client.collection_exists(self.collection):
                return []
            vector = await self.embedder.embed_text(query)

            flt = None
            if department:
                flt = qmodels.Filter(must=[qmodels.FieldCondition(
                    key="department", match=qmodels.MatchValue(value=department),
                )])

            hits = await client.query_points(
                collection_name=self.collection, query=vector, limit=top_k,
                query_filter=flt, with_payload=True, score_threshold=min_score or None,
            )
        except Exception as exc:
            raise KnowledgeBaseError(f"Qdrant search failed: {exc}") from exc

        return [self._to_chunk(p.payload or {}, p.score) for p in hits.points]

    @staticmethod
    def _to_chunk(payload: dict[str, Any], score: float) -> RetrievedChunk:
        return RetrievedChunk(
            text=payload.get("text", ""),
            score=float(score),
            doc_id=payload.get("doc_id", ""),
            title=payload.get("title", "Untitled"),
            url=payload.get("url", "") or "",
            department=payload.get("department", "general"),
            source_type=payload.get("source_type", ""),
            is_official=bool(payload.get("is_official", False)),
            fetched_at=payload.get("fetched_at"),
            metadata=payload,
        )

    async def count(self) -> int:
        try:
            client = await self._get_client()
            if not await client.collection_exists(self.collection):
                return 0
            return (await client.count(self.collection, exact=True)).count
        except Exception:
            return 0

    # -- writes (used only by ingestion and admin approval) ----------------
    async def upsert_chunks(self, chunks: list[dict[str, Any]]) -> int:
        """Insert pre-embedded chunks. Each dict needs `id`, `vector`, `payload`."""
        if not chunks:
            return 0
        client = await self._get_client()
        await self.ensure_collection()
        await client.upsert(
            collection_name=self.collection,
            points=[
                qmodels.PointStruct(id=c["id"], vector=c["vector"], payload=c["payload"])
                for c in chunks
            ],
        )
        return len(chunks)

    async def delete_by_doc_id(self, doc_id: str) -> None:
        client = await self._get_client()
        if not await client.collection_exists(self.collection):
            return
        await client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(filter=qmodels.Filter(
                must=[qmodels.FieldCondition(
                    key="doc_id", match=qmodels.MatchValue(value=doc_id))]
            )),
        )
