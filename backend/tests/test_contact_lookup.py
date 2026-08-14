"""Department contact lookup tests.

"What's the number for Public Works?" has one correct answer that is already
configured. Routing it through retrieval let a mediocre similarity score turn a
known fact into a clarifying question, which is a bad answer to a definite
question.
"""

from __future__ import annotations

import pytest

from app.routing.departments import get_departments
from app.services.contact_lookup import contact_answer, is_contact_question


@pytest.mark.parametrize("text,expected_phone", [
    ("What is the phone number for Public Works?", "516-465-4003"),
    ("What's the phone number of the Building Department?", "516-465-4040"),
    ("Who do I call about a pothole?", "516-465-4003"),
    ("Who do I call about garbage?", "516-465-4031"),
    ("Who do I call about a building permit?", "516-465-4040"),
    ("How do I contact Recreation?", "516-465-4075"),
    ("What number do I call for my water bill?", "516-465-4166"),
    ("Who do I call about my property taxes?", "516-465-4166"),
    ("Who do I call about a tree branch?", "516-465-4003"),
])
def test_contact_questions_return_the_right_number(text, expected_phone):
    answer = contact_answer(text)
    assert answer is not None, f"no contact answer for {text!r}"
    assert expected_phone in answer


def test_topic_beats_question_phrasing():
    """Regression: "who do i call" is itself a keyword of the general
    department, and as a four-word phrase it outscored "pothole" — sending
    callers to the switchboard instead of Public Works."""
    assert "516-465-4003" in contact_answer("Who do I call about a pothole?")


@pytest.mark.parametrize("text", [
    "Where is the Building Department located?",   # address, not phone
    "What are the Recycling Center hours?",        # hours, not phone
    "When is garbage collection?",                 # not a contact question
    "I need a building permit",
    "Can I speak to a person?",
])
def test_non_contact_questions_fall_through(text):
    """The fast path must not hijack questions with real answers."""
    assert contact_answer(text) is None


def test_emergencies_never_get_a_department_number():
    """An emergency must reach the safety notice, not a switchboard."""
    assert contact_answer("There is a gas leak emergency, who do I call?") is None


def test_article_reads_naturally():
    """"reach Building Department" is not English; "reach Recreation" is."""
    assert "the Building Department" in contact_answer(
        "What's the phone number of the Building Department?")
    answer = contact_answer("How do I contact Recreation?")
    assert answer is not None and "the Recreation" not in answer


def test_every_department_has_a_reachable_number():
    """A department with no number would silently fall through to retrieval."""
    for dept in get_departments().all():
        assert dept.phone, f"{dept.id} has no phone number configured"
        assert dept.phone.count("-") == 2, f"{dept.id} phone looks malformed"


def test_is_contact_question_ignores_long_rambles():
    assert not is_contact_question(
        "I was wondering whether the Village has any policy about what happens "
        "when a contractor leaves debris on the street after finishing work"
    )
