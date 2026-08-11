"""Confidence and safety tests (§25, §10, §32).

These are the most important tests in the project. They assert that the system
refuses rather than guesses.
"""

from __future__ import annotations

import pytest

from app.models.db import ConfidenceLevel
from app.providers.base import RetrievedChunk
from app.rag.retriever import RetrievalResult
from app.services.confidence import Action, ConfidenceEngine, load_confidence_config


@pytest.fixture(scope="module")
def engine():
    return ConfidenceEngine()


def chunk(score: float, department="sanitation", official=True, text="Collection is Wednesday.") -> RetrievedChunk:
    return RetrievedChunk(
        text=text, score=score, doc_id="d1", title="Sanitation",
        department=department, is_official=official,
    )


def result(*scores: float, department="sanitation") -> RetrievalResult:
    return RetrievalResult(
        chunks=[chunk(s, department) for s in scores], query="test",
    )


# --------------------------------------------------------------------------
# Policy restrictions — hard overrides that bypass scoring entirely
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("question,expected_policy", [
    ("Can I sue the village over my sidewalk?", "legal_advice"),
    ("Do I need a lawyer for this?", "legal_advice"),
    ("Is it legal to build a second story?", "code_interpretation"),
    ("Will I be grandfathered in?", "code_interpretation"),
    ("How much do I owe on my account?", "individual_account"),
    ("What is my permit status?", "individual_account"),
    ("There is a gas leak on my street", "emergency"),
    ("Someone is hurt, I need an ambulance", "emergency"),
])
async def test_policy_restrictions_force_escalation(engine, question, expected_policy):
    assessment = await engine.assess(
        question, result(0.95, 0.94, 0.93),   # deliberately excellent retrieval
        routing_confidence=0.99, routed_department="sanitation",
        draft_answer="Here is a confident answer.",
    )
    assert assessment.action is Action.ESCALATE
    assert assessment.level is ConfidenceLevel.LOW
    assert assessment.policy is not None
    assert assessment.policy.id == expected_policy
    # Strong retrieval must not be able to override a policy restriction.
    assert assessment.score == 0.0


@pytest.mark.asyncio
async def test_emergency_sets_the_safety_notice_flag(engine):
    assessment = await engine.assess(
        "There is a fire emergency", result(0.9),
        draft_answer="x", routed_department="general",
    )
    assert assessment.policy is not None
    assert assessment.policy.immediate_safety_notice is True


def test_emergency_patterns_are_configured():
    """A municipal system must never try to handle an emergency itself."""
    rules = {r["id"]: r for r in load_confidence_config()["policy_restrictions"]}
    assert "emergency" in rules
    assert rules["emergency"].get("immediate_safety_notice") is True


# --------------------------------------------------------------------------
# Empty and weak retrieval
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_retrieval_escalates(engine):
    assessment = await engine.assess("Anything at all?", RetrievalResult(chunks=[]))
    assert assessment.action is Action.ESCALATE
    assert assessment.level is ConfidenceLevel.LOW
    assert assessment.score == 0.0


@pytest.mark.asyncio
async def test_weak_retrieval_does_not_reach_high_confidence(engine):
    assessment = await engine.assess(
        "Something obscure", result(0.36, 0.35, 0.34),
        routing_confidence=0.2, routed_department="general",
        draft_answer=None,
    )
    assert assessment.level is not ConfidenceLevel.HIGH


# --------------------------------------------------------------------------
# The grounding critic
# --------------------------------------------------------------------------

class FakeLLM:
    """Stands in for the model so grounding verdicts can be tested exactly."""

    def __init__(self, verdict: str):
        self.verdict = verdict

    async def complete(self, *_args, **_kwargs):
        from app.providers.base import LLMResponse
        return LLMResponse(
            text=f'{{"verdict":"{self.verdict}","unsupported_claims":[]}}',
            model="fake",
        )


@pytest.mark.asyncio
async def test_declined_draft_forces_escalation_despite_good_retrieval():
    """Regression for a real failure.

    A nonsense question retrieved documents scoring 0.70 (inflated by query
    expansion) and the model drafted "I don't have that information." Because
    a refusal asserts nothing false, the grounding critic scored it as
    supported and the turn was returned as a HIGH-confidence ANSWER.

    A refusal is evidence the knowledge base LACKS the answer. It must escalate.
    """
    engine = ConfidenceEngine(llm=FakeLLM("declines"))
    assessment = await engine.assess(
        "What is the airspeed velocity of an unladen swallow?",
        result(0.75, 0.72, 0.70),
        routing_confidence=0.8, routed_department="general",
        draft_answer="I don't have information about that.",
    )
    assert assessment.action is Action.ESCALATE
    assert assessment.level is ConfidenceLevel.LOW
    assert assessment.signals["grounding"]["declined"] is True


@pytest.mark.asyncio
async def test_unsupported_grounding_lowers_confidence():
    supported = ConfidenceEngine(llm=FakeLLM("supported"))
    unsupported = ConfidenceEngine(llm=FakeLLM("unsupported"))
    args = ("When is garbage day?", result(0.8, 0.7, 0.6))
    kwargs = dict(routing_confidence=0.9, routed_department="sanitation",
                  draft_answer="Garbage is collected on Wednesday.")

    good = await supported.assess(*args, **kwargs)
    bad = await unsupported.assess(*args, **kwargs)
    assert good.score > bad.score


@pytest.mark.asyncio
async def test_grounding_failure_is_not_treated_as_success():
    """If the critic itself errors, we must not assume the answer was fine."""
    class BrokenLLM:
        async def complete(self, *_a, **_k):
            raise RuntimeError("model unavailable")

    engine = ConfidenceEngine(llm=BrokenLLM())
    assessment = await engine.assess(
        "When is garbage day?", result(0.8, 0.7, 0.6),
        routing_confidence=0.9, routed_department="sanitation",
        draft_answer="Garbage is collected on Wednesday.",
    )
    grounding = assessment.signals["grounding"]
    assert grounding["checked"] is False
    # The configured neutral-pessimistic default, not full credit.
    expected = load_confidence_config()["signals"]["grounding"]["error_default"]
    assert grounding["normalized"] == expected


@pytest.mark.asyncio
async def test_partial_grounding_is_penalized_heavily():
    """Regression: at 0.5 credit, a draft that added the unsourced phrase
    "and animals" still scored HIGH. Partial support means invented detail."""
    weights = load_confidence_config()["signals"]["grounding"]
    assert weights["partial"] <= 0.35, (
        "partial grounding credit is too generous; fabricated detail will pass"
    )


# --------------------------------------------------------------------------
# Signal independence
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confidence_uses_multiple_independent_signals(engine):
    assessment = await engine.assess(
        "When is garbage collected?", result(0.85, 0.7, 0.6),
        routing_confidence=0.9, routed_department="sanitation",
        draft_answer=None,
    )
    for signal in ("top_score", "score_margin", "support_count", "department_agreement"):
        assert signal in assessment.signals, f"missing signal: {signal}"
    assert "weights_applied" in assessment.signals


@pytest.mark.asyncio
async def test_department_disagreement_reduces_confidence(engine):
    """Retrieved documents from a different department than the router chose
    means one of the two is wrong; confidence should reflect that."""
    agreeing = await engine.assess(
        "q", result(0.8, 0.75, 0.7, department="sanitation"),
        routing_confidence=0.9, routed_department="sanitation",
    )
    disagreeing = await engine.assess(
        "q", result(0.8, 0.75, 0.7, department="parking"),
        routing_confidence=0.9, routed_department="sanitation",
    )
    assert agreeing.score > disagreeing.score


@pytest.mark.asyncio
async def test_thresholds_are_ordered(engine):
    """HIGH must be strictly above MEDIUM, or the action ladder is incoherent."""
    from app.core.config import settings
    assert 0 < settings.confidence_medium < settings.confidence_high < 1
