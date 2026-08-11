"""Embeddings via Ollama.

Using Ollama for embeddings (rather than sentence-transformers) is a
deliberate dependency choice: it keeps PyTorch out of the project entirely.
The prototype's whole Python environment is ~800MB instead of ~3.5GB, and
there is one less model runtime to install, warm up, and hold in RAM.

Model: nomic-embed-text — 137M params, 768 dimensions, strong retrieval
quality for its size, 274MB on disk.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import settings
from app.core.errors import ServiceUnavailableError
from app.providers.base import EmbeddingProvider, HealthState, HealthStatus

log = logging.getLogger(__name__)


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.dimension = settings.embedding_dim
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(60.0, connect=5.0),
            )
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> HealthStatus:
        try:
            vec = await self.embed_text("health check")
        except Exception as exc:
            return HealthStatus(
                name="embedding", state=HealthState.UNAVAILABLE,
                detail=f"Embedding model unavailable: {exc}",
                hint=f"Run:  ollama pull {self.model}",
                meta={"model": self.model},
            )

        if len(vec) != self.dimension:
            # A dimension mismatch silently corrupts retrieval, so surface it
            # loudly rather than letting it become mysterious bad answers.
            return HealthStatus(
                name="embedding", state=HealthState.DEGRADED,
                detail=f"Model returns {len(vec)} dims but EMBEDDING_DIM={self.dimension}.",
                hint=f"Set EMBEDDING_DIM={len(vec)} in .env and re-run ingestion.",
                meta={"model": self.model, "actual_dim": len(vec)},
            )

        return HealthStatus(
            name="embedding", state=HealthState.OK,
            detail=f"{self.model} ready ({self.dimension} dims)",
            meta={"model": self.model, "dimension": self.dimension},
        )

    async def embed_text(self, text: str) -> list[float]:
        try:
            r = await self.client.post(
                "/api/embeddings", json={"model": self.model, "prompt": text},
            )
            r.raise_for_status()
        except httpx.ConnectError as exc:
            raise ServiceUnavailableError(
                "Ollama", str(exc), hint="Run:  brew services start ollama",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ServiceUnavailableError(
                "Ollama embeddings",
                f"HTTP {exc.response.status_code}",
                hint=f"Run:  ollama pull {self.model}",
            ) from exc
        return r.json().get("embedding", [])

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts with bounded concurrency.

        Ollama serializes inference internally; a small semaphore keeps the
        request queue healthy without flooding it during bulk ingestion.
        """
        sem = asyncio.Semaphore(4)

        async def one(t: str) -> list[float]:
            async with sem:
                return await self.embed_text(t)

        return await asyncio.gather(*(one(t) for t in texts))
