"""Department routing tests (§25).

The four cases named in the specification are covered explicitly:
    pothole          -> Public Works
    park reservation -> Recreation
    building permit  -> Building
    water bill       -> Finance

These run without the LLM (`allow_llm=False`) so they test the deterministic
rules — the layer that must be correct, auditable, and instant.
"""

from __future__ import annotations

import pytest

from app.routing.departments import GENERAL
from app.routing.router import IntentRouter


@pytest.fixture(scope="module")
def router():
    return IntentRouter()


async def route(router, text: str) -> str:
    decision = await router.classify(text, allow_llm=False)
    return decision.department


# --------------------------------------------------------------------------
# The specification's four cases
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Where do I report a pothole?",
    "There is a huge pothole on my street",
    "The road needs to be repaved",
    "A tree branch fell in the street",
    "My street light is out",
    "When does snow plowing start?",
])
async def test_public_works(router, text):
    assert await route(router, text) == "public_works"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "I want to make a park reservation",
    "How do I reserve a pavilion?",
    "What are the pool hours?",
    "Sign my child up for summer camp",
    "Is there a senior center program?",
])
async def test_recreation(router, text):
    assert await route(router, text) == "recreation"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "I need a building permit",
    "How do I apply for a building permit?",
    "Do I need a permit for a fence?",
    "I want to build a deck",
    "How do I get a certificate of occupancy?",
    "I need to schedule an inspection",
])
async def test_building(router, text):
    assert await route(router, text) == "building"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "How do I pay my water bill?",
    "When is my tax bill due?",
    "I have a question about my property assessment",
    "Can I pay my village bill online?",
])
async def test_finance(router, text):
    assert await route(router, text) == "finance"


# --------------------------------------------------------------------------
# Remaining departments
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("text,expected", [
    ("When is garbage collected?", "sanitation"),
    ("My recycling wasn't picked up", "sanitation"),
    ("How do I dispose of a mattress?", "sanitation"),
    ("I need a parking permit", "parking"),
    ("How do I get railroad parking?", "parking"),
    ("I got a parking ticket", "parking"),
    ("I need a copy of a birth certificate", "village_clerk"),
    ("How do I file a FOIL request?", "village_clerk"),
    ("I want to have a block party", "permits"),
    ("Do I need a permit for a tag sale?", "permits"),
])
async def test_other_departments(router, text, expected):
    assert await route(router, text) == expected


# --------------------------------------------------------------------------
# Disambiguation — the cases keyword matching gets wrong without help
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_building_permit_beats_generic_permit(router):
    """"permit" alone belongs to `permits`; "building permit" must not."""
    assert await route(router, "I need a building permit") == "building"
    assert await route(router, "I need a parking permit") == "parking"


@pytest.mark.asyncio
async def test_justice_court_is_not_recreation(router):
    """Regression: "court" once matched Recreation's tennis-court keyword,
    routing Justice Court questions to the parks department."""
    assert await route(router, "I have a question about Justice Court") != "recreation"
    assert await route(router, "when is my court date") != "recreation"


@pytest.mark.asyncio
async def test_water_bill_vs_water_main(router):
    """Same noun, different department: billing is Finance, infrastructure is
    Public Works."""
    assert await route(router, "my water bill is too high") == "finance"
    assert await route(router, "there is a water main break") == "public_works"


@pytest.mark.asyncio
async def test_pool_permit_vs_pool_hours(router):
    assert await route(router, "what are the pool hours") == "recreation"
    assert await route(router, "I need a pool permit") == "building"


# --------------------------------------------------------------------------
# Confidence and fallback behavior
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unmatched_text_falls_back_to_general_with_low_confidence(router):
    decision = await router.classify(
        "What is the airspeed velocity of an unladen swallow?", allow_llm=False,
    )
    assert decision.department == GENERAL
    assert decision.confidence < 0.4


@pytest.mark.asyncio
async def test_clear_match_is_confident(router):
    decision = await router.classify("Where do I report a pothole?", allow_llm=False)
    assert decision.department == "public_works"
    assert decision.confidence >= 0.6


@pytest.mark.asyncio
async def test_empty_input_is_handled(router):
    decision = await router.classify("", allow_llm=False)
    assert decision.department == GENERAL
    assert decision.confidence == 0.0


@pytest.mark.asyncio
async def test_decision_serializes_for_the_api(router):
    payload = (await router.classify("I need a building permit", allow_llm=False)).as_dict()
    assert payload["department"] == "building"
    assert payload["department_name"] == "Building Department"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert "method" in payload


# --------------------------------------------------------------------------
# Department registry
# --------------------------------------------------------------------------

def test_all_nine_departments_exist(departments):
    expected = {
        "general", "public_works", "recreation", "building", "village_clerk",
        "finance", "sanitation", "parking", "permits",
    }
    assert expected == set(departments.ids)


def test_contact_details_are_not_invented(departments):
    """Phone numbers and emails must stay null until sourced from the official
    Village directory. Inventing them would be worse than omitting them."""
    for department in departments.all():
        assert department.phone is None, f"{department.id} has an unverified phone number"
        assert department.email is None, f"{department.id} has an unverified email"
