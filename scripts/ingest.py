#!/usr/bin/env python
"""Build the knowledge base index.

    python scripts/ingest.py             # incremental: replace changed docs
    python scripts/ingest.py --recreate  # wipe and rebuild from scratch
    python scripts/ingest.py --no-web    # local knowledge/ files only

IMPORTANT — embedded Qdrant holds an exclusive lock on ./data/qdrant, so the
API server must be stopped while this runs. The script detects a running server
and tells you rather than failing with a confusing lock error.

To avoid the stop/start dance entirely, run Qdrant in server mode:
    docker compose -f docker/docker-compose.yml up -d qdrant
    # then set QDRANT_URL=http://localhost:6333 in .env
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import settings          # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.models.database import init_db        # noqa: E402

log = logging.getLogger("ingest")


async def server_is_running() -> bool:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(
                f"http://{settings.api_host}:{settings.api_port}/api/health/live"
            )
            return r.status_code == 200
    except Exception:
        return False


async def main(args: argparse.Namespace) -> int:
    configure_logging()

    if settings.uses_embedded_qdrant and await server_is_running():
        print(
            "\n  The API server is running and holds the embedded Qdrant lock.\n"
            "\n  Stop it, then re-run this script:\n"
            "      pkill -f 'uvicorn app.main:app'\n"
            "      python scripts/ingest.py\n"
            "\n  Or switch to Qdrant server mode to skip this step entirely:\n"
            "      docker compose -f docker/docker-compose.yml up -d qdrant\n"
            "      # set QDRANT_URL=http://localhost:6333 in .env\n",
            file=sys.stderr,
        )
        return 2

    settings.ensure_directories()
    await init_db()

    from app.providers.factory import registry
    from app.rag.pipeline import IngestionPipeline

    health = await registry.embedding.health()
    if not health.healthy:
        print(f"\n  Embedding model unavailable: {health.detail}\n  {health.hint}\n",
              file=sys.stderr)
        return 1

    pipeline = IngestionPipeline()
    report = await pipeline.run(
        recreate=args.recreate,
        include_web=not args.no_web,
        include_files=not args.no_files,
        include_approved=not args.no_approved,
    )
    print(report.summary())

    await registry.shutdown()

    if report.errors and report.chunks == 0:
        return 1
    if report.chunks == 0:
        print("\n  Nothing was indexed. Run scripts/crawl.py first.\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Garden City knowledge.")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete the collection and rebuild from scratch.")
    parser.add_argument("--no-web", action="store_true",
                        help="Skip crawled Village web pages.")
    parser.add_argument("--no-files", action="store_true",
                        help="Skip local knowledge/ files.")
    parser.add_argument("--no-approved", action="store_true",
                        help="Skip admin-approved database entries.")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
