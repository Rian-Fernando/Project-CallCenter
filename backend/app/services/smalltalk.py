"""Conversational openers and closers.

A receptionist that answers "hello" with "I don't have enough verified
information to answer that accurately" is not a receptionist. These utterances
are handled deterministically — no retrieval, no model call, no escalation, and
critically no entry in the human review queue, which must stay a list of real
knowledge gaps.

Responses are fixed strings rather than generated, because they are spoken on
every single call and must be identical, instant, and impossible to get wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SmalltalkMatch:
    kind: str
    reply: str
    ends_call: bool = False


GREETING_REPLY = (
    "Hello, and thank you for calling the Village of Garden City. "
    "How can I help you today?"
)
GREETING_MID_CALL = "Of course. What else can I help you with?"
THANKS_REPLY = "You're very welcome. Is there anything else I can help you with?"
GOODBYE_REPLY = "Thank you for calling the Village of Garden City. Have a good day."
ARE_YOU_HUMAN_REPLY = (
    "I'm an automated assistant for the Village of Garden City. "
    "I can answer questions using published Village information, and I can "
    "connect you with a department whenever you'd like to speak with a person. "
    "What can I help you with?"
)
CAPABILITY_REPLY = (
    "I can help with questions about Village services, things like sanitation "
    "and recycling, parking permits, building permits, recreation, and tax or "
    "water billing. What would you like to know?"
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("goodbye", re.compile(
        r"^\s*(bye|goodbye|good bye|thats all|that's all|that is all|"
        r"nothing else|no thanks?( you)?|im good|i'm good|were done|we're done|"
        r"hang up|end (the )?call)\s*[.!]*\s*$", re.I)),
    ("thanks", re.compile(
        r"^\s*(thanks?|thank you|thx|appreciate it|got it|perfect|great|"
        r"awesome|ok thanks?|okay thanks?)\s*[.!]*\s*$", re.I)),
    ("greeting", re.compile(
        r"^\s*(hi|hey|hello|yo|howdy|good (morning|afternoon|evening)|"
        r"hi there|hello there|anyone there|are you there)\s*[.!,]*\s*$", re.I)),
    # Built from an optional-modifier stem rather than a flat alternation:
    # the flat version missed "are you a real person" because it enumerated
    # "real" and "a person" separately but never their combination.
    ("identity", re.compile(
        r"^\s*("
        r"(are|is) (you|this) (a |an )?(real |actual |live )?"
        r"(human|person|robot|bot|ai|machine|computer|recording|human being)"
        r"|am i (talking|speaking) (to|with) (a |an )?(real )?"
        r"(human|person|robot|bot|ai|machine|computer)"
        r"|who are you|what are you|are you human|are you real"
        r")\s*[?.!]*\s*$", re.I)),
    ("capability", re.compile(
        r"^\s*(what can you (do|help( me)? with)|how can you help( me)?|"
        r"what do you do|what are you for|help)\s*[?.!]*\s*$", re.I)),
]

_REPLIES = {
    "thanks": (THANKS_REPLY, False),
    "goodbye": (GOODBYE_REPLY, True),
    "identity": (ARE_YOU_HUMAN_REPLY, False),
    "capability": (CAPABILITY_REPLY, False),
}


# Openers that name a topic without actually asking anything:
#   "I have a question about garbage collection."
#   "I'm calling about my water bill."
# These are not answerable — there is no question yet — but they are also not
# knowledge gaps. Treating them as unanswerable escalated the opening line of
# the primary demo flow. A receptionist would simply ask what the caller
# wants to know, which is what the specification's own example shows.
_TOPIC_ANNOUNCEMENT = re.compile(
    r"^\s*(?:hi|hello|hey)?[,.\s]*"
    r"(?:i(?:'m| am)?\s+)?"
    r"(?:have|had|got)?\s*"
    r"(?:a |an |some )?"
    r"(?:quick |general |couple of |few )?"
    r"(?:question|questions|inquiry|query|concern)s?\s+"
    r"(?:about|regarding|on|with|concerning)\s+(?P<topic>.{2,80}?)"
    r"\s*[.?!]*\s*$",
    re.IGNORECASE,
)
_CALLING_ABOUT = re.compile(
    r"^\s*(?:hi|hello|hey)?[,.\s]*"
    r"i(?:'m| am)?\s+(?:calling|contacting you|reaching out|here)\s+"
    r"(?:about|regarding|concerning|to ask about)\s+(?P<topic>.{2,80}?)"
    r"\s*[.?!]*\s*$",
    re.IGNORECASE,
)
_NEED_HELP_WITH = re.compile(
    r"^\s*(?:hi|hello|hey)?[,.\s]*"
    r"i\s+(?:need|want|would like)\s+(?:some\s+)?"
    r"(?:help|assistance|information|info)\s+"
    r"(?:about|with|on|regarding)\s+(?P<topic>.{2,80}?)"
    r"\s*[.?!]*\s*$",
    re.IGNORECASE,
)


def match_topic_announcement(text: str) -> str | None:
    """Return the announced topic, or None.

    The caller has named a subject but not asked anything. The right response
    is one short question inviting them to continue — never an answer, and
    never an escalation.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped.split()) > 16:
        return None
    for pattern in (_TOPIC_ANNOUNCEMENT, _CALLING_ABOUT, _NEED_HELP_WITH):
        if match := pattern.match(stripped):
            topic = match.group("topic").strip(" .?!,")
            # Reject fragments that carry no subject of their own.
            if len(topic) >= 3 and topic.lower() not in {"it", "that", "this", "something"}:
                return topic
    return None


def topic_reply(topic: str, department_name: str | None = None) -> str:
    """Invite the caller to ask their actual question."""
    if department_name and department_name != "General Village Information":
        return (
            f"Sure, I can help with {topic}. That's handled by {department_name}. "
            f"What would you like to know?"
        )
    return f"Sure, I can help with {topic}. What would you like to know?"


def match_smalltalk(text: str, *, has_history: bool = False) -> SmalltalkMatch | None:
    """Classify an utterance as pure conversational filler, or return None.

    Matching is anchored to the whole utterance on purpose. "Hi, when is
    garbage day?" is a real question that happens to start with a greeting, and
    must go through the normal pipeline.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped.split()) > 8:
        return None

    for kind, pattern in _PATTERNS:
        if not pattern.match(stripped):
            continue
        if kind == "greeting":
            # Mid-call greetings shouldn't replay the full opening script.
            return SmalltalkMatch(
                kind, GREETING_MID_CALL if has_history else GREETING_REPLY,
            )
        reply, ends = _REPLIES[kind]
        return SmalltalkMatch(kind, reply, ends_call=ends)
    return None
