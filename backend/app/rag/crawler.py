"""Polite crawler for the official Village of Garden City website.

ETHICS AND POLITENESS — these are requirements, not options:
  * robots.txt is fetched first and every Disallow rule is honored.
  * Requests are serialized with a configurable delay (default 1.0s). There is
    no concurrency; we never open parallel connections to the Village server.
  * The User-Agent identifies the project honestly.
  * Pages are cached to disk, so re-running ingestion re-fetches nothing.
  * Only sitemap-listed pages are visited. We do not follow links, spider
    blindly, probe for hidden paths, or touch anything behind a login.
  * A hard page cap (CRAWL_MAX_PAGES) bounds total load.

PRIORITIZATION
  The Village sitemap contains ~305 URLs, and a large share are dated
  construction-project news posts (dozens of "LIRR Third Track Update" items).
  Those are stale news, not answers a receptionist needs. URLs are therefore
  scored for service relevance and the highest-value pages are crawled first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.routing.departments import GENERAL, get_departments

log = logging.getLogger(__name__)

# Pages matching these patterns are dated news/project archives. They are
# crawled last (and usually not at all) because they answer no FAQ.
DEPRIORITIZE = re.compile(
    r"(lirr|third-track|grade-crossing|update---|update-\w+-20\d\d|"
    r"\b20(1[5-9]|2[0-5])-information\b|pseg|national-grid|dutch-broadway|"
    r"test-pitting|gas-main|time-lapse|scorecard|mobilization|"
    r"environmental-impact|contaminated-groundw|monitoring-wells|"
    r"westerman|prior-proposals|historical-cost|department-history|"
    r"village-historians|news-links|commissioners-corner|meeting-videos)",
    re.IGNORECASE,
)

# Pages that directly answer resident questions. Crawled first.
PRIORITIZE = re.compile(
    r"(department|sanitation|recycling|public-works|highway|parking|permit|"
    r"building|recreation|park|pool|senior|library|water|sewer|tax|bill|pay|"
    r"clerk|license|licensing|application|forms|apply-for|find|sign-up|"
    r"regulation|local-laws|codes|zoning|planning|contact|new-resident|"
    r"request-for-service|faq|justice-court|employment|volunteer|"
    r"traffic|street-lighting|engineering|garage|service-yard|fire|police)",
    re.IGNORECASE,
)

# Boilerplate that appears on every page of this CMS and pollutes retrieval.
NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^\s*skip to (main )?content\s*$",
        r"^\s*(home|search|menu|close|back to top|print|share|translate)\s*$",
        r"^\s*copyright\s*©?\s*\d{4}.*$",
        r"^\s*powered by\b.*$",
        r"^\s*all rights reserved\.?\s*$",
        r"^\s*(facebook|twitter|instagram|youtube|linkedin)\s*$",
        r"^\s*arrow (left|right)\s*$",
        r"^\s*\d+\s*$",
    )
]


@dataclass
class CrawledPage:
    url: str
    title: str
    text: str
    department: str
    fetched_at: str
    content_hash: str
    priority: float = 0.0
    links: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


class VillageCrawler:
    def __init__(
        self,
        base_url: str | None = None,
        sitemap_url: str | None = None,
        *,
        max_pages: int | None = None,
        delay: float | None = None,
        cache_dir: Path | None = None,
    ):
        self.base_url = (base_url or settings.village_base_url).rstrip("/")
        self.sitemap_url = sitemap_url or settings.village_sitemap_url
        self.max_pages = max_pages or settings.crawl_max_pages
        self.delay = delay if delay is not None else settings.crawl_delay_seconds
        self.cache_dir = cache_dir or (settings.knowledge_dir / "_crawled")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = settings.crawl_user_agent
        self._robots: RobotFileParser | None = None
        self._last_request = 0.0
        self.departments = get_departments()

    # ------------------------------------------------------------------
    # Politeness
    # ------------------------------------------------------------------
    async def load_robots(self, client: httpx.AsyncClient) -> None:
        url = urljoin(self.base_url + "/", "robots.txt")
        parser = RobotFileParser()
        parser.set_url(url)
        try:
            r = await client.get(url, timeout=15.0)
            if r.status_code == 200:
                parser.parse(r.text.splitlines())
                log.info("robots.txt loaded from %s", url)
            else:
                # No robots.txt is permission by convention, but we stay
                # conservative and keep the same rate limit either way.
                log.warning("robots.txt returned %s — proceeding with rate limiting only.",
                            r.status_code)
                parser.parse([])
        except Exception as exc:
            log.warning("Could not fetch robots.txt (%s) — proceeding cautiously.", exc)
            parser.parse([])
        self._robots = parser

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        try:
            # Check both our UA and "*" so a wildcard rule is never bypassed.
            return (self._robots.can_fetch(self.user_agent, url)
                    and self._robots.can_fetch("*", url))
        except Exception:
            return False

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    async def fetch_sitemap(self, client: httpx.AsyncClient) -> list[str]:
        try:
            await self._throttle()
            r = await client.get(self.sitemap_url, timeout=30.0)
            r.raise_for_status()
        except Exception as exc:
            log.error("Could not fetch sitemap %s: %s", self.sitemap_url, exc)
            return []

        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text, re.IGNORECASE)
        host = urlparse(self.base_url).netloc
        # Stay strictly on the Village's own domain.
        return [u for u in dict.fromkeys(urls) if urlparse(u).netloc == host]

    def prioritize(self, urls: list[str]) -> list[tuple[str, float, str]]:
        """Score and sort URLs. Returns (url, score, department) triples."""
        scored: list[tuple[str, float, str]] = []
        for url in urls:
            slug = unquote_slug(url)
            score = 1.0
            if PRIORITIZE.search(slug):
                score += 3.0
            if DEPRIORITIZE.search(slug):
                score -= 4.0

            dept_scores = self.departments.score_text(slug.replace("-", " "))
            department = GENERAL
            if dept_scores:
                department = max(dept_scores, key=dept_scores.get)
                score += min(2.0, dept_scores[department])

            # Short slugs are usually top-level landing pages ("Sanitation");
            # long ones are usually dated news items.
            if len(slug.split("-")) <= 4:
                score += 0.5

            scored.append((url, round(score, 3), department))

        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored

    def classify_page(
        self, url: str, title: str, text: str, *, url_guess: str = GENERAL,
    ) -> str:
        """Assign a department to a crawled page.

        Delegates to the shared classifier so the crawler and the ingestion
        loader always agree. Note that the loader re-runs this at ingestion
        time, so classification-rule changes do not require a re-crawl.
        """
        return self.departments.classify_content(
            title=title,
            slug=unquote_slug(url).replace("-", " ").replace("/", " "),
            body=text,
            default=url_guess,
        )

    # ------------------------------------------------------------------
    # Fetch + extract
    # ------------------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.json"

    def _read_cache(self, url: str) -> CrawledPage | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            return CrawledPage(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.debug("Discarding unreadable cache entry %s", path)
            return None

    def _write_cache(self, page: CrawledPage) -> None:
        self._cache_path(page.url).write_text(
            json.dumps(page.__dict__, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    async def fetch_page(
        self, client: httpx.AsyncClient, url: str, department: str,
        *, use_cache: bool = True,
    ) -> CrawledPage | None:
        if use_cache and (cached := self._read_cache(url)) is not None:
            return cached

        if not self.allowed(url):
            log.info("robots.txt disallows %s — skipping", url)
            return None

        try:
            await self._throttle()
            r = await client.get(url, timeout=30.0)
            if r.status_code != 200:
                log.debug("HTTP %s for %s", r.status_code, url)
                return None
            if "html" not in r.headers.get("content-type", "").lower():
                return None
        except Exception as exc:
            log.debug("Fetch failed for %s: %s", url, exc)
            return None

        title, text = extract_content(r.text)
        if len(text) < 180:
            # Nav-only or empty page; nothing worth embedding.
            return None

        page = CrawledPage(
            url=url, title=title or slug_title(url), text=text,
            department=self.classify_page(url, title, text, url_guess=department),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
        )
        self._write_cache(page)
        return page

    async def crawl(self, *, use_cache: bool = True) -> list[CrawledPage]:
        headers = {"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"}
        pages: list[CrawledPage] = []

        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            await self.load_robots(client)

            urls = await self.fetch_sitemap(client)
            if not urls:
                log.error("Sitemap produced no URLs — nothing to crawl.")
                return []

            ranked = self.prioritize(urls)
            allowed = [(u, s, d) for u, s, d in ranked if self.allowed(u)]
            blocked = len(ranked) - len(allowed)
            targets = allowed[: self.max_pages]

            log.info(
                "Sitemap: %d URLs | %d blocked by robots.txt | crawling top %d "
                "at %.1fs intervals (~%.0fs)",
                len(ranked), blocked, len(targets), self.delay,
                len(targets) * self.delay,
            )

            for i, (url, score, department) in enumerate(targets, 1):
                page = await self.fetch_page(client, url, department, use_cache=use_cache)
                if page:
                    page.priority = score
                    pages.append(page)
                if i % 25 == 0:
                    log.info("  ... %d/%d fetched (%d usable)", i, len(targets), len(pages))

        log.info("Crawl complete: %d usable pages from %d attempts", len(pages), len(targets))
        return pages


# ----------------------------------------------------------------------
# HTML extraction
# ----------------------------------------------------------------------

def extract_content(html: str) -> tuple[str, str]:
    """Pull a page title and readable body text out of raw HTML."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "form", "iframe", "svg", "button", "aside"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if h1 := soup.find("h1"):
        if h1_text := h1.get_text(strip=True):
            title = h1_text
    # "Sanitation | Garden City, NY" -> "Sanitation"
    title = re.sub(r"\s*[|–—-]\s*(Garden City,? NY.*|Official Website.*)$", "",
                   title, flags=re.IGNORECASE).strip()

    main = (soup.find("main") or soup.find(attrs={"role": "main"})
            or soup.find(id=re.compile(r"content|main", re.I))
            or soup.find(class_=re.compile(r"content|main", re.I))
            or soup.body or soup)

    lines: list[str] = []
    for raw in main.get_text("\n", strip=True).split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 2:
            continue
        if any(p.match(line) for p in NOISE_PATTERNS):
            continue
        lines.append(line)

    # Collapse consecutive duplicates (repeated menu labels).
    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)

    return title, "\n".join(deduped).strip()


def unquote_slug(url: str) -> str:
    return urlparse(url).path.strip("/")


def slug_title(url: str) -> str:
    """Turn '/204/Sanitation' into 'Sanitation'."""
    parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
    if not parts:
        return "Village of Garden City"
    return re.sub(r"[-_]+", " ", parts[-1]).strip().title()
