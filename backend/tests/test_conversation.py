"""Conversation memory, smalltalk, and end-to-end turn tests (§25, §12)."""

from __future__ import annotations

import uuid

import pytest

from app.services.memory import Session, SessionStore, Turn
from app.services.smalltalk import match_smalltalk
from tests.conftest import requires_kb


# --------------------------------------------------------------------------
# Follow-up detection
# --------------------------------------------------------------------------

def session_with_history() -> Session:
    session = Session(session_id="s1")
    session.add_turn(Turn(
        user="I have a question about garbage pickup.",
        assistant="Sure, what would you like to know?",
        department="sanitation",
    ))
    return session


@pytest.mark.parametrize("text", [
    "When is mine?", "What about that?", "How do I do that?",
    "And the recycling?", "When?", "Is it today?",
])
def test_referential_followups_are_detected(text):
    assert session_with_history().needs_context(text) is True


@pytest.mark.parametrize("text", [
    "How do I apply for a building permit for a new deck on my property?",
    "What are the requirements for a railroad parking permit application?",
])
def test_standalone_questions_do_not_need_context(text):
    assert session_with_history().needs_context(text) is False


def test_first_utterance_never_needs_context():
    """With no history there is nothing to resolve against."""
    assert Session(session_id="s").needs_context("When is mine?") is False


def test_contextual_query_includes_the_prior_turn():
    """"When is mine?" retrieves nothing on its own; the topic must be folded in."""
    query = session_with_history().contextual_query("When is mine?")
    assert "garbage" in query.lower()
    assert "when is mine" in query.lower()


def test_session_tracks_the_current_department():
    assert session_with_history().current_department == "sanitation"


# --------------------------------------------------------------------------
# Session store
# --------------------------------------------------------------------------

def test_sessions_are_isolated_from_each_other():
    """Privacy boundary: one caller's context must never leak into another's."""
    store = SessionStore()
    a = store.get_or_create("a")
    b = store.get_or_create("b")
    a.add_turn(Turn(user="garbage question", assistant="", department="sanitation"))
    assert b.turns == []
    assert b.current_department is None


def test_ending_a_session_discards_its_context():
    store = SessionStore()
    store.get_or_create("x").add_turn(Turn(user="hello", assistant="hi"))
    store.end("x")
    assert store.get("x") is None


def test_history_is_capped():
    session = Session(session_id="s")
    for i in range(50):
        session.add_turn(Turn(user=f"question {i}", assistant="answer"))
    assert len(session.turns) <= 20


# --------------------------------------------------------------------------
# Smalltalk
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("hello", "greeting"), ("hi there", "greeting"),
    ("thanks", "thanks"), ("thank you", "thanks"),
    ("bye", "goodbye"), ("that's all", "goodbye"),
    ("are you a real person", "identity"), ("are you human", "identity"),
    ("who are you", "identity"), ("what can you do", "capability"),
])
def test_smalltalk_is_recognized(text, kind):
    match = match_smalltalk(text)
    assert match is not None and match.kind == kind


@pytest.mark.parametrize("text", [
    "hi, when is garbage day?",
    "when is garbage collection",
    "thanks, but how do I get a permit?",
    "hello I need to report a pothole",
])
def test_real_questions_are_not_swallowed_as_smalltalk(text):
    """Regression: a greeting-prefixed question must reach the full pipeline."""
    assert match_smalltalk(text) is None


def test_greeting_does_not_replay_the_script_mid_call():
    opening = match_smalltalk("hello", has_history=False)
    later = match_smalltalk("hello", has_history=True)
    assert opening and later and opening.reply != later.reply


def test_goodbye_ends_the_call():
    match = match_smalltalk("goodbye")
    assert match is not None and match.ends_call is True


# --------------------------------------------------------------------------
# End-to-end turns
# --------------------------------------------------------------------------

@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_greeting_skips_the_pipeline_entirely():
    """Regression: "hello" once ran full retrieval, escalated to a department,
    and filed itself in the human review queue."""
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    result = await conversation_service.handle(uuid.uuid4().hex, "hello", channel="demo")
    assert result.action == "answer"
    assert result.escalation is None
    assert result.intent.startswith("smalltalk")
    assert result.timings["total_ms"] < 500


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_grounded_answer_carries_official_citations():
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    result = await conversation_service.handle(
        uuid.uuid4().hex, "When is garbage collection?", channel="demo",
    )
    assert result.department == "sanitation"
    assert result.sources, "a grounded answer must cite its sources"
    assert any(s["is_official"] for s in result.sources)


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_question_escalates_and_is_queued_for_review():
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    result = await conversation_service.handle(
        uuid.uuid4().hex,
        "What is the airspeed velocity of an unladen swallow?",
        channel="demo",
    )
    assert result.action == "escalate"
    assert result.confidence_level == "low"
    assert result.escalation is not None
    assert result.escalation["simulated"] is True
    assert result.escalation["recommended_action"]


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_followup_resolves_against_the_previous_turn():
    """The §12 scenario: "when is mine?" must stay on the garbage topic."""
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    session_id = uuid.uuid4().hex
    first = await conversation_service.handle(
        session_id, "I have a question about garbage pickup.", channel="demo",
    )
    assert first.department == "sanitation"

    second = await conversation_service.handle(session_id, "When is mine?", channel="demo")
    assert second.used_context is True
    assert second.department == "sanitation"


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_policy_restricted_question_escalates_without_calling_the_model():
    """Policy hits must be fast — no retrieval, no generation."""
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    result = await conversation_service.handle(
        uuid.uuid4().hex, "Can I sue the village over a sidewalk?", channel="demo",
    )
    assert result.action == "escalate"
    assert result.escalation["reason_code"] == "legal_advice"
    assert result.timings["total_ms"] < 4000


# --------------------------------------------------------------------------
# Topic announcements (§12, §36)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_topic", [
    ("Hi, I have a question about garbage collection.", "garbage collection"),
    ("I have a question about my water bill", "my water bill"),
    ("I'm calling about a building permit", "a building permit"),
    ("I need help with parking permits", "parking permits"),
    ("I had a question regarding recycling", "recycling"),
])
def test_topic_announcements_are_detected(text, expected_topic):
    from app.services.smalltalk import match_topic_announcement
    assert match_topic_announcement(text) == expected_topic


@pytest.mark.parametrize("text", [
    "When is garbage collection?",
    "How do I get a building permit?",
    "Where do I report a pothole?",
    "hello",
])
def test_real_questions_are_not_topic_announcements(text):
    from app.services.smalltalk import match_topic_announcement
    assert match_topic_announcement(text) is None


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_topic_announcement_invites_rather_than_escalating():
    """Regression for the primary demo flow (§36).

    "Hi, I have a question about garbage collection." names a topic but asks
    nothing. Running it through retrieval produced a refusal, an escalation,
    and a spurious review-queue entry for the opening line of a normal call.
    """
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    result = await conversation_service.handle(
        uuid.uuid4().hex,
        "Hi, I have a question about garbage collection.",
        channel="demo",
    )
    assert result.action == "clarify"
    assert result.escalation is None
    assert result.department == "sanitation"
    assert "what would you like to know" in result.answer.lower()


@requires_kb
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_demo_flow_topic_then_followup():
    """The complete §36 sequence in one session."""
    from app.models.database import init_db
    from app.services.conversation import conversation_service

    await init_db()
    session_id = uuid.uuid4().hex

    opener = await conversation_service.handle(
        session_id, "Hi, I have a question about garbage collection.", channel="demo",
    )
    assert opener.action == "clarify"

    followup = await conversation_service.handle(session_id, "When is mine?", channel="demo")
    assert followup.action == "answer"
    assert followup.department == "sanitation"
    assert followup.used_context is True
    assert followup.sources
