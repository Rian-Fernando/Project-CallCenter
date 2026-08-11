"""The document model that flows through the ingestion pipeline.

    SOURCE -> load -> clean -> chunk -> embed -> vector store -> retrieval

`SourceDocument` is the unit produced by loaders; `Chunk` is what gets embedded.
Both carry the provenance needed to cite an answer (§7).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# Any document whose `is_official` flag is False must be labeled with this
# string wherever it is shown to a user (§8).
DEMO_LABEL = "DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION"


@dataclass
class SourceDocument:
    title: str
    text: str
    department: str = "general"
    source_type: str = "markdown"      # web | pdf | markdown | txt | html | faq | admin
    source_path: str | None = None
    source_url: str | None = None
    is_official: bool = False
    fetched_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    @property
    def doc_id(self) -> str:
        """Stable id derived from identity, not content.

        Deriving this from URL/path rather than text means re-ingesting an
        edited page replaces its chunks instead of duplicating them.
        """
        seed = self.source_url or self.source_path or f"{self.department}/{self.title}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class Chunk:
    text: str
    doc_id: str
    chunk_index: int
    title: str
    department: str
    source_type: str
    source_url: str | None = None
    source_path: str | None = None
    is_official: bool = False
    fetched_at: str | None = None

    @property
    def chunk_id(self) -> str:
        return hashlib.sha256(
            f"{self.doc_id}:{self.chunk_index}".encode()
        ).hexdigest()[:32]

    def to_payload(self) -> dict[str, Any]:
        """Everything stored alongside the vector.

        The payload is deliberately self-sufficient: a retrieved chunk can be
        rendered as a citation without a second database lookup.
        """
        return {
            "text": self.text,
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "title": self.title,
            "department": self.department,
            "source_type": self.source_type,
            "url": self.source_url or "",
            "path": self.source_path or "",
            "is_official": self.is_official,
            "fetched_at": self.fetched_at,
        }


def normalize_question(text: str) -> str:
    """Normalize a question for duplicate detection in the review queue.

    Lowercase, strip punctuation and filler words so that
    "When is my garbage picked up?" and "when does garbage get picked up"
    collapse to the same key.
    """
    low = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    stop = {
        "the", "a", "an", "is", "are", "do", "does", "did", "i", "my", "me",
        "can", "could", "would", "should", "please", "tell", "know", "want",
        "to", "of", "for", "in", "on", "at", "get", "got", "there", "here",
        "what", "when", "where", "how", "who", "which", "and", "or", "you",
    }
    words = [w for w in low.split() if w and w not in stop]
    return " ".join(sorted(set(words)))[:400]
