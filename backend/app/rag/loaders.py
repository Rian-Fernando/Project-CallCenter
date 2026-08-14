"""Document loaders — the entry point of the ingestion pipeline (§7).

Supported sources:
  * crawled Village web pages  (knowledge/_crawled/*.json)
  * Markdown                   (.md)
  * plain text                 (.txt)
  * PDF                        (.pdf)
  * HTML                       (.html/.htm)
  * structured FAQ data        (.faq.json / .json)
  * admin-approved answers     (from the database, via load_knowledge_entries)

Department is taken from the file's parent directory under `knowledge/`, so
`knowledge/sanitation/collection.md` is a Sanitation document. Front-matter or
JSON fields can override that.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.rag.cleaning import clean_text, looks_substantive
from app.rag.documents import SourceDocument
from app.routing.departments import GENERAL, get_departments

log = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}
ALL_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".json"}


# ----------------------------------------------------------------------
# Crawled Village pages
# ----------------------------------------------------------------------

def load_crawled(cache_dir: Path | None = None) -> list[SourceDocument]:
    """Load cached crawl output, collapsing duplicate content.

    The Village FAQ module maps ~30 distinct `FAQ.aspx?QID=n` URLs onto a
    handful of category pages, so the same text arrives many times. We keep one
    copy per unique content hash and prefer the shortest, most canonical URL as
    its citation link.
    """
    cache_dir = cache_dir or (settings.knowledge_dir / "_crawled")
    if not cache_dir.exists():
        log.info("No crawl cache at %s — skipping web documents.", cache_dir)
        return []

    by_hash: dict[str, dict] = {}
    for path in sorted(cache_dir.glob("*.json")):
        try:
            page = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Skipping unreadable crawl cache file %s", path.name)
            continue

        digest = page.get("content_hash") or ""
        existing = by_hash.get(digest)
        if existing is None or len(page.get("url", "")) < len(existing.get("url", "")):
            by_hash[digest] = page

    registry = get_departments()
    docs: list[SourceDocument] = []
    for page in by_hash.values():
        text = clean_text(page.get("text", ""))
        if not looks_substantive(text):
            continue
        title = page.get("title") or "Village of Garden City"
        url = page.get("url", "")
        # Re-classify here rather than trusting the value stored at crawl time,
        # so improvements to the rules apply without re-fetching the site.
        department = registry.classify_content(
            title=title,
            slug=urlparse(url).path.replace("-", " ").replace("/", " "),
            body=text,
            default=page.get("department", GENERAL),
        )
        docs.append(SourceDocument(
            title=title,
            text=text,
            department=department,
            source_type="web",
            source_url=page.get("url"),
            # Content fetched from gardencityny.net is official Village
            # material. This is the flag that separates it from demo data.
            is_official=True,
            fetched_at=page.get("fetched_at"),
        ))

    log.info("Loaded %d unique web documents (from %d cached pages)",
             len(docs), len(list(cache_dir.glob('*.json'))))
    return docs


# ----------------------------------------------------------------------
# Local knowledge files
# ----------------------------------------------------------------------

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse optional YAML front matter from a markdown file."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        import yaml
        meta = yaml.safe_load(match.group(1)) or {}
    except Exception:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[match.end():]


def _sidecar_meta(path: Path) -> dict:
    """Read `<file>.meta.json` if present.

    Markdown carries front matter, but PDFs and HTML cannot. Without a sidecar
    an official Village PDF would default to `is_official: false` and be
    labeled DEMO DATA — wrong, and it would undercut a real citation.
    """
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("Could not read %s: %s", sidecar.name, exc)
        return {}


def _department_for(path: Path, root: Path) -> str:
    registry = get_departments()
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return GENERAL
    for part in parts[:-1]:
        if registry.exists(part):
            return part
    return GENERAL


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        log.warning("pypdf unavailable, skipping %s: %s", path.name, exc)
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        # Malformed PDFs are common; never let one abort the whole ingestion.
        log.warning("Could not read PDF %s: %s", path.name, exc)
        return ""


def _read_html(path: Path) -> tuple[str, str]:
    from app.rag.crawler import extract_content
    try:
        return extract_content(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        log.warning("Could not read HTML %s: %s", path.name, exc)
        return "", ""


def _read_faq_json(path: Path, department: str) -> list[SourceDocument]:
    """Load structured FAQ data.

    Accepts either a list of {question, answer} objects or
    {"department": ..., "faqs": [...]}.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not parse JSON %s: %s", path.name, exc)
        return []

    if isinstance(data, dict):
        department = data.get("department", department)
        official = bool(data.get("is_official", False))
        entries = data.get("faqs") or data.get("questions") or []
        source_url = data.get("source_url")
    else:
        entries, official, source_url = data, False, None

    docs: list[SourceDocument] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        question = (entry.get("question") or entry.get("q") or "").strip()
        answer = (entry.get("answer") or entry.get("a") or "").strip()
        if not (question and answer):
            continue
        # Keep the question in the embedded text: resident phrasing matches
        # question wording far more often than it matches answer wording.
        docs.append(SourceDocument(
            title=question,
            text=f"Question: {question}\nAnswer: {answer}",
            department=entry.get("department", department),
            source_type="faq",
            source_path=str(path),
            source_url=entry.get("source_url") or source_url,
            is_official=bool(entry.get("is_official", official)),
            extra={"faq_index": i},
        ))
    return docs


def load_knowledge_dir(root: Path | None = None) -> list[SourceDocument]:
    root = root or settings.knowledge_dir
    if not root.exists():
        log.warning("Knowledge directory %s does not exist.", root)
        return []

    docs: list[SourceDocument] = []
    for path in sorted(root.rglob("*")):
        # `_crawled` is handled by load_crawled(); skip hidden/dot dirs.
        if not path.is_file() or path.suffix.lower() not in ALL_EXTENSIONS:
            continue
        if any(part.startswith("_") or part.startswith(".") for part in path.parts):
            continue

        department = _department_for(path, root)
        suffix = path.suffix.lower()

        try:
            if path.name.endswith(".meta.json"):
                continue  # sidecar metadata, not a document
            if suffix == ".json":
                docs.extend(_read_faq_json(path, department))
                continue

            if suffix == ".pdf":
                meta = _sidecar_meta(path)
                raw = _read_pdf(path)
                title = meta.get("title") or path.stem.replace("_", " ").replace("-", " ").title()
            elif suffix in {".html", ".htm"}:
                meta = _sidecar_meta(path)
                title, raw = _read_html(path)
                title = meta.get("title") or title or path.stem.replace("_", " ").title()
            else:
                content = path.read_text(encoding="utf-8", errors="replace")
                meta, raw = _parse_frontmatter(content)
                title = meta.get("title") or _first_heading(raw) or \
                    path.stem.replace("_", " ").replace("-", " ").title()
        except Exception as exc:
            log.warning("Skipping %s: %s", path.name, exc)
            continue

        # A sidecar may declare a file citable-but-not-indexed. Dense reference
        # PDFs are the case this exists for: they are the authority a curated
        # extract points at, but indexing them buries the extract.
        if meta.get("index") is False:
            log.info("Skipping %s (sidecar sets index: false)", path.name)
            continue

        text = clean_text(raw)
        if not looks_substantive(text):
            log.debug("Skipping %s: too little content after cleaning.", path.name)
            continue

        docs.append(SourceDocument(
            title=title,
            text=text,
            department=meta.get("department", department),
            source_type={"pdf": "pdf"}.get(suffix.lstrip("."), None)
            or ("html" if suffix in {".html", ".htm"} else
                "txt" if suffix == ".txt" else "markdown"),
            source_path=str(path.relative_to(settings.repo_root)),
            source_url=meta.get("source_url"),
            # Local files are demo data unless they explicitly declare
            # otherwise. Defaulting to False is the safe direction: an
            # unlabeled document is never presented as official.
            is_official=bool(meta.get("is_official", False)),
            fetched_at=meta.get("fetched_at"),
        ))

    log.info("Loaded %d documents from %s", len(docs), root)
    return docs


def _first_heading(text: str) -> str | None:
    for line in text.split("\n")[:12]:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


# ----------------------------------------------------------------------
# Admin-approved answers (§15)
# ----------------------------------------------------------------------

def knowledge_entry_to_document(entry) -> SourceDocument:
    """Convert an admin-approved KnowledgeEntry row into an indexable document."""
    return SourceDocument(
        title=entry.question,
        text=f"Question: {entry.question}\nAnswer: {entry.answer}",
        department=entry.department,
        source_type="admin",
        source_url=entry.source_url,
        # Falls back to a synthetic identity so `doc_id` stays unique and
        # stable per entry: re-approving an edited answer replaces its chunks
        # instead of creating a second copy.
        source_path=entry.source_document or f"admin://{entry.id}",
        is_official=bool(entry.is_official),
        fetched_at=(entry.updated_at or datetime.now(timezone.utc)).isoformat(),
        extra={"entry_id": entry.id, "approved_by": entry.approved_by},
    )


def load_all() -> list[SourceDocument]:
    """Every file-based source. Database entries are added by the pipeline."""
    return load_crawled() + load_knowledge_dir()
