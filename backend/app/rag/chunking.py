"""Chunking — split documents into embeddable passages.

Uses LlamaIndex's SentenceSplitter so chunks break on sentence boundaries
rather than mid-word, which measurably improves retrieval quality over naive
fixed-width slicing.

Each chunk is prefixed with its document title and department. That context
survives into the embedding, so a chunk reading "Collection is Wednesday"
still matches a query about *sanitation* even though the passage itself never
says the word.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.rag.documents import Chunk, SourceDocument
from app.routing.departments import get_departments

log = logging.getLogger(__name__)

_splitter = None


def _get_splitter():
    global _splitter
    if _splitter is None:
        from llama_index.core.node_parser import SentenceSplitter
        _splitter = SentenceSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
            paragraph_separator="\n\n",
        )
    return _splitter


def chunk_document(doc: SourceDocument) -> list[Chunk]:
    if not doc.text.strip():
        return []

    department_name = get_departments().name_of(doc.department)
    header = f"[{department_name}] {doc.title}"

    try:
        pieces = _get_splitter().split_text(doc.text)
    except Exception as exc:
        # Never let one odd document break a whole ingestion run.
        log.warning("Splitter failed on '%s' (%s); falling back to paragraphs.",
                    doc.title, exc)
        pieces = _fallback_split(doc.text, settings.rag_chunk_size)

    chunks: list[Chunk] = []
    for i, piece in enumerate(pieces):
        body = piece.strip()
        if len(body) < 40:
            continue
        chunks.append(Chunk(
            text=f"{header}\n\n{body}",
            doc_id=doc.doc_id,
            chunk_index=i,
            title=doc.title,
            department=doc.department,
            source_type=doc.source_type,
            source_url=doc.source_url,
            source_path=doc.source_path,
            is_official=doc.is_official,
            fetched_at=doc.fetched_at,
        ))
    return chunks


def _fallback_split(text: str, size: int) -> list[str]:
    out: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 <= size:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            if buf:
                out.append(buf)
            buf = para[:size] if len(para) > size else para
    if buf:
        out.append(buf)
    return out


def chunk_documents(docs: list[SourceDocument]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(chunk_document(doc))
    log.info("Chunked %d documents into %d passages", len(docs), len(chunks))
    return chunks
