"""RAG tests (§25): correct document retrieved, irrelevant rejected, citations kept."""

from __future__ import annotations

import pytest

from app.providers.base import RetrievedChunk
from app.rag.chunking import chunk_document
from app.rag.cleaning import clean_text, looks_substantive
from app.rag.documents import DEMO_LABEL, SourceDocument, normalize_question
from app.rag.retriever import RetrievalResult, Retriever
from tests.conftest import requires_kb


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def test_cleaning_removes_cms_placeholder_text():
    """The Village CMS ships unedited "Answer goes here..." placeholders in its
    FAQ module; embedding them pollutes retrieval."""
    raw = "Real content about collection.\nAnswer goes here...\nMore real content."
    cleaned = clean_text(raw)
    assert "Answer goes here" not in cleaned
    assert "Real content about collection." in cleaned


def test_cleaning_removes_navigation_boilerplate():
    raw = "Skip to main content\nHome\nSanitation is collected Wednesday.\nRead more\n"
    cleaned = clean_text(raw)
    assert "Skip to main content" not in cleaned
    assert "Read more" not in cleaned
    assert "Sanitation is collected Wednesday." in cleaned


def test_cleaning_collapses_repeated_lines():
    raw = "\n".join(["Departments"] * 9 + ["Rubbish is collected each Wednesday."])
    cleaned = clean_text(raw)
    assert cleaned.count("Departments") <= 1
    assert "Rubbish is collected each Wednesday." in cleaned


def test_substantive_filter_rejects_stubs():
    assert not looks_substantive("Too short.")
    assert looks_substantive(
        "The Sanitation Division collects household garbage twice each week and "
        "rubbish at the curb every Wednesday, except as noted on the holiday "
        "schedule published by the Village each year."
    )


# --------------------------------------------------------------------------
# Chunking and provenance
# --------------------------------------------------------------------------

def test_chunks_retain_source_metadata():
    """Citations must survive chunking — an answer without provenance is
    unusable in a municipal context."""
    doc = SourceDocument(
        title="Sanitation Collection Schedule",
        text=("Rubbish is collected at the curb each Wednesday. " * 40),
        department="sanitation", source_type="web",
        source_url="https://www.gardencityny.net/204/Sanitation",
        is_official=True, fetched_at="2026-08-09T00:00:00Z",
    )
    chunks = chunk_document(doc)
    assert chunks
    for c in chunks:
        assert c.source_url == "https://www.gardencityny.net/204/Sanitation"
        assert c.department == "sanitation"
        assert c.is_official is True
        payload = c.to_payload()
        assert payload["url"] and payload["title"] and payload["doc_id"]


def test_chunk_text_carries_department_context():
    """A passage reading only "Collection is Wednesday" must still be findable
    from a sanitation query, so the header is embedded with it."""
    doc = SourceDocument(
        title="Collection Schedule", text="Collection is Wednesday. " * 40,
        department="sanitation",
    )
    assert "Sanitation" in chunk_document(doc)[0].text


def test_doc_id_is_stable_across_content_edits():
    """Re-ingesting an edited page must replace its chunks, not duplicate them."""
    base = dict(title="Sanitation", department="sanitation",
                source_url="https://www.gardencityny.net/204/Sanitation")
    a = SourceDocument(text="Original text.", **base)
    b = SourceDocument(text="Edited text.", **base)
    assert a.doc_id == b.doc_id
    assert a.content_hash != b.content_hash


def test_admin_entries_get_unique_doc_ids():
    a = SourceDocument(title="Q1", text="A1", department="building",
                       source_type="admin", source_path="admin://entry-1")
    b = SourceDocument(title="Q2", text="A2", department="building",
                       source_type="admin", source_path="admin://entry-2")
    assert a.doc_id != b.doc_id


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------

def test_source_payload_exposes_official_flag():
    official = RetrievedChunk(text="t", score=0.8, title="Sanitation",
                              url="https://example.gov", department="sanitation",
                              is_official=True).as_source()
    demo = RetrievedChunk(text="t", score=0.8, title="Placeholder",
                          department="permits", is_official=False).as_source()
    assert official["is_official"] is True
    assert demo["is_official"] is False


def test_context_block_labels_demo_data():
    """The model must be able to tell official content from placeholders, so it
    can warn the resident rather than presenting demo data as policy."""
    demo = RetrievedChunk(text="Placeholder content.", score=0.8,
                          title="Demo", department="permits", is_official=False)
    block = RetrievalResult(chunks=[demo]).context_block()
    assert DEMO_LABEL in block


def test_sources_are_deduplicated():
    chunks = [
        RetrievedChunk(text="a", score=0.9, title="Sanitation",
                       url="https://x/204", department="sanitation"),
        RetrievedChunk(text="b", score=0.8, title="Sanitation",
                       url="https://x/204", department="sanitation"),
        RetrievedChunk(text="c", score=0.7, title="Recycling",
                       url="https://x/203", department="sanitation"),
    ]
    assert len(RetrievalResult(chunks=chunks).sources()) == 2


def test_score_margin_detects_a_flat_distribution():
    """A flat score distribution means we matched a topic but no specific fact —
    the classic setup for fabrication."""
    sharp = RetrievalResult(chunks=[
        RetrievedChunk(text="", score=s, department="x") for s in (0.9, 0.6, 0.5)
    ])
    flat = RetrievalResult(chunks=[
        RetrievedChunk(text="", score=s, department="x") for s in (0.62, 0.61, 0.60)
    ])
    assert sharp.score_margin > flat.score_margin


# --------------------------------------------------------------------------
# Question normalization (review-queue deduplication)
# --------------------------------------------------------------------------

def test_equivalent_questions_normalize_together():
    a = normalize_question("When is my garbage picked up?")
    b = normalize_question("when does the garbage get picked up")
    assert a == b


def test_different_questions_stay_distinct():
    assert normalize_question("When is garbage collected?") != \
        normalize_question("How do I get a building permit?")


# --------------------------------------------------------------------------
# Retrieval against the real index
# --------------------------------------------------------------------------

@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_department", [
    ("When is garbage collection?", "sanitation"),
    ("How do I pay my water bill?", "finance"),
    ("I need a building permit", "building"),
    ("How do I get a railroad parking permit?", "parking"),
])
async def test_retrieves_the_right_department(query, expected_department):
    result = await Retriever().retrieve(query, department=expected_department)
    assert not result.is_empty
    assert result.top_score > 0.5
    assert result.chunks[0].department == expected_department


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_irrelevant_query_scores_far_below_relevant_ones():
    """The separation between real questions and nonsense is what the
    confidence engine depends on."""
    relevant = await Retriever().retrieve("When is garbage collection?")
    irrelevant = await Retriever().retrieve(
        "What is the airspeed velocity of an unladen swallow?"
    )
    assert relevant.top_score > irrelevant.top_score + 0.15


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieved_chunks_have_citations():
    result = await Retriever().retrieve("When is recycling collected?")
    assert not result.is_empty
    for source in result.sources():
        assert source["title"]
        assert "is_official" in source
        if source["is_official"]:
            assert source["url"].startswith("http")


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_expansion_cannot_inflate_an_irrelevant_query():
    """Regression: expanding with a department description once raised a
    nonsense query's top score from 0.48 to 0.70, and it got answered.

    The department boost is legitimate and still applies, so the ceiling is the
    literal score plus that boost — not the raw literal score.
    """
    from app.rag.retriever import DEPARTMENT_BOOST, OFFICIAL_BOOST

    nonsense = "What is the airspeed velocity of an unladen swallow?"
    plain = await Retriever().retrieve(nonsense)
    expanded = await Retriever().retrieve(nonsense, department="public_works")

    ceiling = plain.top_score * DEPARTMENT_BOOST * OFFICIAL_BOOST
    assert expanded.top_score <= ceiling + 1e-6
    # And the absolute value must stay far below anything answerable.
    assert expanded.top_score < 0.62


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_expansion_still_improves_recall_for_real_questions():
    """The cap must not neuter the feature it protects.

    "pothole" appears nowhere in the Village corpus; the Highway Division page
    says "road repairs". Expansion is what bridges that gap.
    """
    result = await Retriever().retrieve(
        "Where do I report a pothole?", department="public_works",
    )
    assert not result.is_empty
    assert result.chunks[0].department == "public_works"
    assert result.top_score > 0.55
