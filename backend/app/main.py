"""Village of Garden City — AI Receptionist API.

PROOF OF CONCEPT. Not production software. See SECURITY_ROADMAP.md for what
would need to change before this could handle real municipal callers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin as admin_api
from app.api import chat as chat_api
from app.api import health as health_api
from app.api import voice as voice_api
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.models.database import close_db, init_db
from app.providers.factory import registry

configure_logging()
log = logging.getLogger(__name__)

DESCRIPTION = """
AI receptionist for the Village of Garden City, New York.

**This is a proof of concept.** All information served by this API comes from
an ingested knowledge base. Documents marked `is_official: false` are
**DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION**.

The system is designed to refuse rather than guess: when retrieval confidence
is low, it escalates to a human department instead of generating an answer.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 68)
    log.info("  Village of Garden City — AI Receptionist  [%s]", settings.app_env)
    log.info("=" * 68)

    settings.ensure_directories()
    await init_db()

    # Probe providers at boot so problems surface here, in the developer's
    # terminal, rather than mid-demo in front of an audience.
    statuses = await registry.health_all()
    for name, status in statuses.items():
        icon = {"ok": "OK  ", "degraded": "WARN", "unavailable": "FAIL"}[status.state.value]
        log.info("  [%s] %-13s %s", icon, name, status.detail)
        if status.hint and status.state.value != "ok":
            log.info("         -> %s", status.hint)

    log.info("-" * 68)
    log.info("  API:  http://%s:%d", settings.api_host, settings.api_port)
    log.info("  Docs: http://%s:%d/docs", settings.api_host, settings.api_port)
    log.info("=" * 68)

    yield

    await registry.shutdown()
    await close_db()
    log.info("Shutdown complete")


app = FastAPI(
    title="Garden City AI Receptionist",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(health_api.router, prefix="/api")
app.include_router(chat_api.router, prefix="/api")
app.include_router(voice_api.router, prefix="/api")
app.include_router(admin_api.router, prefix="/api")


@app.get("/", tags=["meta"], summary="Service banner")
async def root() -> dict:
    return {
        "service": "Garden City AI Receptionist",
        "version": "0.1.0",
        "status": "proof-of-concept",
        "disclaimer": (
            "Demonstration system. Responses may draw on placeholder data that "
            "is not official Village information."
        ),
        "docs": "/docs",
        "health": "/api/health",
    }
