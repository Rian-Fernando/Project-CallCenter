#!/usr/bin/env python
"""Crawl official Village of Garden City web pages into the local cache.

    python scripts/crawl.py              # use cache where available
    python scripts/crawl.py --refresh    # re-fetch every page
    python scripts/crawl.py --max 50     # limit page count
    python scripts/crawl.py --dry-run    # show what would be fetched

POLITENESS: obeys robots.txt, one request at a time, 1s delay by default, and
only visits URLs listed in the Village sitemap. Please don't lower the delay.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging  # noqa: E402
from app.rag.crawler import VillageCrawler      # noqa: E402


async def main(args: argparse.Namespace) -> int:
    configure_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    crawler = VillageCrawler(max_pages=args.max, delay=args.delay)

    if args.dry_run:
        import httpx
        async with httpx.AsyncClient(
            headers={"User-Agent": crawler.user_agent}, follow_redirects=True
        ) as client:
            await crawler.load_robots(client)
            urls = await crawler.fetch_sitemap(client)
        ranked = [t for t in crawler.prioritize(urls) if crawler.allowed(t[0])]
        print(f"\n  {len(urls)} sitemap URLs; would fetch top {min(args.max, len(ranked))}:\n")
        for url, score, dept in ranked[: args.max]:
            print(f"   {score:6.2f}  {dept:<14} {url}")
        return 0

    pages = await crawler.crawl(use_cache=not args.refresh)
    if not pages:
        print("\n  No pages were retrieved. Check network access.\n", file=sys.stderr)
        return 1

    print(f"\n{'=' * 62}")
    print(f"  CRAWL COMPLETE — {len(pages)} pages, "
          f"{sum(p.char_count for p in pages):,} characters")
    print(f"  Cache: {crawler.cache_dir}")
    print(f"{'=' * 62}")
    for dept, n in Counter(p.department for p in pages).most_common():
        print(f"    {dept:<18} {n:>4}")
    print(f"{'=' * 62}")
    print("\n  Next:  python scripts/ingest.py\n")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Crawl official Village web pages.")
    p.add_argument("--max", type=int, default=150, help="Maximum pages to fetch.")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between requests.")
    p.add_argument("--refresh", action="store_true", help="Ignore the cache and re-fetch.")
    p.add_argument("--dry-run", action="store_true", help="List targets without fetching.")
    raise SystemExit(asyncio.run(main(p.parse_args())))
