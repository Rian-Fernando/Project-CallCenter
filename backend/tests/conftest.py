"""Shared test fixtures.

Tests are split into two groups:

  unit         — no external services. Always run.
  integration  — need Ollama and an ingested knowledge base. Skipped
                 automatically when those are unavailable, so `pytest` is
                 always green on a fresh checkout rather than failing for
                 environmental reasons.

Run everything:            pytest
Skip slow model tests:     pytest -m "not integration"
Only integration:          pytest -m integration
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402


def _ollama_up() -> bool:
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _kb_ready() -> bool:
    """Is there an ingested knowledge base to retrieve from?"""
    async def check() -> bool:
        from app.providers.factory import registry
        try:
            return await registry.vector_store.count() > 0
        except Exception:
            return False

    try:
        return asyncio.run(check())
    except Exception:
        return False


OLLAMA_AVAILABLE = _ollama_up()
KB_AVAILABLE = OLLAMA_AVAILABLE and _kb_ready()

requires_ollama = pytest.mark.skipif(
    not OLLAMA_AVAILABLE,
    reason="Ollama is not running (start it with: brew services start ollama)",
)
requires_kb = pytest.mark.skipif(
    not KB_AVAILABLE,
    reason="Knowledge base is empty (build it with: ./scripts/ingest.sh)",
)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs Ollama and/or the knowledge base")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def departments():
    from app.routing.departments import get_departments
    return get_departments()
