"""Retrieval — turning a resident's question into cited evidence.

Two behaviors matter more than raw recall here:

1. **Department-aware retrieval without department tunnel vision.** Filtering
   strictly by the routed department is dangerous, because the router can be
   wrong and a hard filter would then guarantee a miss. Instead we retrieve
   globally *and* within the department, merge, and apply a modest boost to
   in-department matches.

2. **Official sources outrank demo data.** When a placeholder and a real
   Village page both match, the official page must win, so the resident sees a
   real citation rather than a document stamped DEMO DATA.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import settings
from app.providers.base import RetrievedChunk, VectorStoreProvider
from app.providers.factory import registry
from app.routing.departments import GENERAL

log = logging.getLogger(__name__)

DEPARTMENT_BOOST = 1.06
OFFICIAL_BOOST = 1.04
# Hits found only via the expanded paraphrase are worth less than hits on the
# resident's literal wording.
EXPANSION_PENALTY = 0.92


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    query: str = ""
    duration_ms: int = 0
    department_filtered: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def top_score(self) -> float:
        return self.chunks[0].score if self.chunks else 0.0

    @property
    def score_margin(self) -> float:
        """Gap between the best match and the third-best.

        A wide margin means the corpus has one clearly best answer. A flat
        distribution means we matched a general topic but no specific fact —
        exactly when a model is most likely to fabricate.
        """
        if len(self.chunks) < 3:
            return self.top_score if self.chunks else 0.0
        return max(0.0, self.chunks[0].score - self.chunks[2].score)

    def above(self, threshold: float) -> list[RetrievedChunk]:
        return [c for c in self.chunks if c.score >= threshold]

    @property
    def departments(self) -> list[str]:
        return [c.department for c in self.chunks]

    def sources(self, limit: int = 4) -> list[dict]:
        """Deduplicated citation list for the API response and the UI."""
        seen: set[str] = set()
        out: list[dict] = []
        for chunk in self.chunks:
            key = chunk.url or f"{chunk.department}/{chunk.title}"
            if key in seen:
                continue
            seen.add(key)
            out.append(chunk.as_source())
            if len(out) >= limit:
                break
        return out

    def context_block(self, limit: int = 5, max_chars: int = 5200) -> str:
        """Format retrieved passages for the LLM prompt.

        Excerpts are numbered so the model can cite them, and each carries its
        official/demo status so the model can tell the resident when an answer
        rests on placeholder data.
        """
        parts: list[str] = []
        used = 0
        for i, chunk in enumerate(self.chunks[:limit], 1):
            label = "OFFICIAL VILLAGE SOURCE" if chunk.is_official else \
                "DEMO DATA — NOT OFFICIAL VILLAGE INFORMATION"
            body = chunk.text.strip()
            if used + len(body) > max_chars:
                body = body[: max(0, max_chars - used)]
            if not body:
                break
            parts.append(
                f"--- EXCERPT {i} ---\n"
                f"Title: {chunk.title}\n"
                f"Department: {chunk.department}\n"
                f"Status: {label}\n"
                f"URL: {chunk.url or 'n/a'}\n\n{body}"
            )
            used += len(body)
        return "\n\n".join(parts)


class Retriever:
    def __init__(self, store: VectorStoreProvider | None = None):
        self._store = store

    @property
    def store(self) -> VectorStoreProvider:
        return self._store or registry.vector_store

    async def retrieve(
        self, query: str, *, department: str | None = None,
        top_k: int | None = None, min_score: float | None = None,
    ) -> RetrievalResult:
        import time
        started = time.perf_counter()

        top_k = top_k or settings.rag_top_k
        min_score = settings.rag_min_score if min_score is None else min_score

        if not (query or "").strip():
            return RetrievalResult(query=query)

        # Scores earned by the resident's ACTUAL wording. These are the honest
        # numbers, and the expansion pass below is never allowed to raise them.
        literal_scores: dict[str, float] = {}
        literal_best = 0.0

        try:
            # Retrieve globally first — never let a wrong routing decision
            # hide the correct document.
            chunks = await self.store.search(query, top_k=top_k * 2, min_score=0.0)

            if department:
                scoped = await self.store.search(
                    query, top_k=top_k, department=department, min_score=0.0,
                )
                chunks = self._merge(chunks, scoped)

                # Vocabulary bridge. Residents and municipal documents use
                # different words for the same thing: a caller says "pothole",
                # the Highway Division page says "road repairs" — and the word
                # "pothole" appears nowhere on the Village site. Re-querying
                # with the department's own vocabulary recovers those
                # documents. This only changes which real documents are found;
                # it never introduces information.
                #
                # Skipped for `general`: it is the catch-all, and its
                # description is generic enough ("Village Hall hours, general
                # contact information...") to match almost any page, which
                # manufactures similarity out of nothing.
                if department != GENERAL and (
                    expanded := self._expand(query, department)
                ) != query:
                    bridged = await self.store.search(
                        expanded, top_k=top_k, department=department, min_score=0.0,
                    )
                    # Expansion is a RECALL tool, never a confidence booster.
                    # It may surface documents the literal wording missed, but
                    # it must never make any match look stronger than the
                    # resident's own words justified.
                    #
                    # Two ways that leaked before this was tightened:
                    #   1. A document found ONLY by the paraphrase inherited the
                    #      paraphrase's (higher) similarity.
                    #   2. A document found by BOTH queries kept the higher of
                    #      the two scores, so the paraphrase silently upgraded it.
                    # Together these lifted an unanswerable question from 0.48
                    # to 0.70 and it got answered.
                    literal_scores = {self._key(c): c.score for c in chunks}
                    literal_best = max(literal_scores.values(), default=0.0)
                    for chunk in bridged:
                        chunk.score *= EXPANSION_PENALTY
                    chunks = self._merge(chunks, bridged)
                    for chunk in chunks:
                        key = self._key(chunk)
                        chunk.score = (
                            # Seen literally: the literal score is authoritative.
                            literal_scores[key] if key in literal_scores
                            # Paraphrase-only: capped at the literal best.
                            else min(chunk.score, literal_best)
                        )
        except Exception as exc:
            log.error("Retrieval failed: %s", exc)
            return RetrievalResult(query=query,
                                   duration_ms=int((time.perf_counter() - started) * 1000))

        for chunk in chunks:
            adjusted = chunk.score
            if department and chunk.department == department:
                adjusted *= DEPARTMENT_BOOST
            if chunk.is_official:
                adjusted *= OFFICIAL_BOOST
            # Cosine similarity is bounded at 1.0; boosts must not break that
            # invariant or the confidence engine's calibration breaks with it.
            adjusted = min(1.0, adjusted)
            # Hard ceiling: once expansion has run, no chunk may exceed the
            # best score the literal query achieved *after* the same boosts.
            # Applying this before boosting was not enough — the department
            # boost lifted capped scores straight back over the line.
            if literal_scores:
                ceiling = literal_best
                if department:
                    ceiling *= DEPARTMENT_BOOST
                ceiling = min(1.0, ceiling * OFFICIAL_BOOST)
                adjusted = min(adjusted, ceiling)
            chunk.score = adjusted

        chunks.sort(key=lambda c: c.score, reverse=True)
        kept = [c for c in chunks if c.score >= min_score][:top_k]

        return RetrievalResult(
            chunks=kept,
            query=query,
            duration_ms=int((time.perf_counter() - started) * 1000),
            department_filtered=bool(department),
        )

    @staticmethod
    def _key(chunk: RetrievedChunk) -> str:
        """Stable identity for a chunk across result sets."""
        return f"{chunk.doc_id}:{chunk.metadata.get('chunk_index', 0)}"

    @staticmethod
    def _expand(query: str, department: str) -> str:
        """Append the department's own vocabulary to the query.

        Uses the configured department name and description from
        departments.yaml — the same text a Village administrator maintains —
        so the bridge vocabulary stays editable without code changes.
        """
        from app.routing.departments import get_departments
        dept = get_departments().get(department)
        vocabulary = " ".join(dept.description.split())
        return f"{query} {dept.name}: {vocabulary}".strip()

    @staticmethod
    def _merge(*groups: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Union of chunk lists, keeping the highest score seen for each chunk."""
        best: dict[str, RetrievedChunk] = {}
        for group in groups:
            for chunk in group:
                key = Retriever._key(chunk)
                if key not in best or chunk.score > best[key].score:
                    best[key] = chunk
        return list(best.values())


retriever = Retriever()
