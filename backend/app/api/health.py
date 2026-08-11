"""Health and readiness endpoints.

`GET /api/health` is the first thing to check when anything misbehaves. It
probes every provider concurrently and, for each failure, returns the exact
command that fixes it.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.providers.base import HealthState
from app.providers.factory import registry

router = APIRouter(tags=["health"])


@router.get("/health", summary="Full system health with remediation hints")
async def health() -> dict:
    statuses = await registry.health_all()

    # `unavailable` on a core service means the system genuinely cannot answer.
    # `degraded` (e.g. empty KB, TTS fallback) is still demoable.
    core = ("llm", "embedding", "vector_store")
    unavailable = [k for k, s in statuses.items()
                   if s.state is HealthState.UNAVAILABLE and k in core]
    degraded = [k for k, s in statuses.items() if s.state is not HealthState.OK]

    overall = ("unavailable" if unavailable
               else "degraded" if degraded else "ok")

    return {
        "status": overall,
        "environment": settings.app_env,
        "ready_for_calls": not unavailable,
        "services": {
            name: {
                "state": s.state.value,
                "detail": s.detail,
                "hint": s.hint,
                "meta": s.meta,
            }
            for name, s in statuses.items()
        },
        "configuration": {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.ollama_model,
            "thinking_mode": settings.ollama_thinking,
            "embedding_model": settings.embedding_model,
            "stt_model": settings.whisper_model,
            "vector_store_mode": "embedded" if settings.uses_embedded_qdrant else "server",
            "database": settings.resolved_database_url.split("://", 1)[0],
            "retention_days": settings.retention_days,
            "gogov_mode": settings.gogov_mode,
        },
    }


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict:
    return {"status": "alive"}
