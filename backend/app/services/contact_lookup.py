"""Direct department contact answers.

"What's the phone number for Public Works?" has one correct answer, and it is
already in config/departments.yaml. Routing it through retrieval and the
confidence engine means a mediocre similarity score can turn a known fact into
a clarifying question — which is exactly what happened in testing:

    Q: What is the phone number for Public Works?
    A: What specific service within Public Works are you looking for?

That is a bad answer to a question with a definite answer. Contact lookups are
handled here instead: deterministic, instant, and impossible to get wrong.

The number still comes from the Village's published directory. Nothing is
invented — if a department has no number configured, this declines and lets the
normal pipeline handle it.
"""

from __future__ import annotations

import re

from app.routing.departments import Department, get_departments

# "What's the number for X", "who do I call about X", "how do I contact X"
_CONTACT_INTENT = re.compile(
    r"\b("
    r"phone\s*(number)?|telephone|number\s+(for|of|to)|"
    r"who\s+(do|should|can)\s+i\s+(call|contact|speak|talk)|"
    r"how\s+(do|can)\s+i\s+(call|contact|reach|get\s+in\s+touch)|"
    r"contact\s+(number|info|information|details)?|"
    r"call\s+(about|regarding|for)|reach\s+(them|him|her|the)"
    r")\b",
    re.IGNORECASE,
)

# Question scaffolding with no topical content of its own.
_FILLER = re.compile(
    r"\b(what|whats|what's|is|the|for|of|to|do|i|a|an|my|about|regarding|"
    r"please|can|you|tell|me|number|phone|call|contact|reach)\b",
    re.IGNORECASE,
)

# Asking for a person, an address, or hours is NOT a department phone lookup.
_NOT_CONTACT = re.compile(
    r"\b(address|located|location|directions|hours|open|close|email|"
    r"who is the|name of the|speak to a (person|human|manager))\b",
    re.IGNORECASE,
)


def is_contact_question(text: str) -> bool:
    """Is the caller asking how to reach a department?"""
    stripped = (text or "").strip()
    if not stripped or len(stripped.split()) > 18:
        return False
    if _NOT_CONTACT.search(stripped):
        return False
    return bool(_CONTACT_INTENT.search(stripped))


def contact_answer(text: str, routed_department: str | None = None) -> str | None:
    """Build a direct contact answer, or None to fall through to the pipeline.

    `routed_department` is the router's decision, used when the caller names no
    department explicitly ("who do I call about a pothole?" → Public Works).
    """
    if not is_contact_question(text):
        return None

    registry = get_departments()

    # Score the TOPIC, not the question's phrasing. "who do i call" is itself a
    # keyword of the general department, and as a four-word phrase it outscored
    # "pothole" — so "Who do I call about a pothole?" returned the main Village
    # switchboard instead of Public Works.
    topic = _CONTACT_INTENT.sub(" ", text)
    topic = _FILLER.sub(" ", topic)
    scores = registry.score_text(topic)
    department: Department | None = None
    if scores:
        best = max(scores, key=scores.get)
        # One solid topic keyword is enough. A single word like "pothole" scores
        # exactly 1.0, and a higher bar sent "Who do I call about a pothole?"
        # to the general Village number instead of Public Works.
        if scores[best] >= 1.0:
            department = registry.get(best)
    if department is None and routed_department:
        department = registry.get(routed_department)
    if department is None or not department.phone:
        return None

    # Emergencies must never be answered with a department number.
    if re.search(r"\b(emergency|911|urgent|right now)\b", text, re.IGNORECASE):
        return None

    # "reach Building Department" is not English; "reach Recreation" is. Add
    # the article only where the name reads as a proper department title.
    name = department.name
    article = "the " if name.split()[-1].lower() in {
        "department", "office", "clerk", "center", "division", "yard",
    } else ""
    return f"You can reach {article}{name} at {department.phone}."
