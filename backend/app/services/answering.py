"""Answer generation — grounded, cited, and willing to say "I don't know".

THE CENTRAL RULE (§32): the assistant may only state facts that appear in the
retrieved excerpts. When the excerpts don't contain the answer, saying so is
the correct output — it is strictly better than a plausible guess about a
municipal policy, fee, or deadline.

Prompts are written for speech: short sentences, no markdown, no bullet lists,
no URLs read aloud. Citations travel separately in the API response and are
rendered visually by the UI.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from app.core.config import settings
from app.providers.base import ChatMessage
from app.providers.factory import registry
from app.rag.retriever import RetrievalResult
from app.routing.departments import get_departments

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the phone receptionist for the Village of Garden City, New York.

RULES
1. State facts ONLY from the EXCERPTS. Never use outside knowledge.
2. If the excerpts don't answer it, say you don't have that detail and name the
   department in DEPARTMENT with its number. Never say "the appropriate
   department" — you know which one.
3. Never invent numbers, fees, dates, hours, or addresses.
4. An excerpt marked DEMO DATA is not official; say it needs confirming.

STYLE (spoken aloud)
- Answer in the FIRST sentence. One to two sentences, then stop.
- Your own plain words, not document phrasing. Say "put your trash out by six
  in the morning", not "refuse shall be placed at the curb".
- Never circular. "Collected on the collection day" says nothing — if you can't
  name the day, time, or amount, say so and give the department.
- If it depends on the caller's address or section, ASK.
- Never say: excerpt, document, source, provided, listed, context.
- No markdown, lists, emoji, or URLs. Say "the Village website".
"""

CLARIFY_PROMPT = """\
You are the automated receptionist for the Village of Garden City, New York.

The available information partially matches what the resident asked, but not
well enough to answer safely. Your job right now is to ask ONE follow-up
question that would narrow it down.

RULES:
- Output ONLY your follow-up question. Nothing else.
- NEVER repeat, echo, or restate the resident's question back to them.
- Ask something genuinely NEW that narrows the request — for example which
  service, which location, which property, or which of two situations applies.
- Do not answer, and do not state any facts or figures.
- One or two sentences, plain speech. No markdown, no lists, no quotation marks.

Example of a GOOD response:
  Are you asking about residential collection or a commercial property?

Example of a BAD response (this echoes the question — never do this):
  Where do I report a pothole?
"""

# Used when the model's clarification is unusable (echoes the question or comes
# back empty). Deterministic so the caller always gets a sensible prompt.
_GENERIC_CLARIFY = (
    "I want to make sure I get this right. Could you tell me a little more "
    "about what you need?"
)


def _clean_for_speech(text: str) -> str:
    """Strip anything that would sound wrong when spoken aloud."""
    text = re.sub(r"^```.*?```$", "", text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"[*_`#]+", "", text)                      # markdown marks
    text = re.sub(r"^\s*[-•*]\s+", "", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", "the Village website", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # md links
    text = re.sub(r"\n{2,}", " ", text)

    # The prompt forbids these, but models leak them under pressure and the
    # phrasing is meaningless to a caller. Rewrite rather than hope.
    text = re.sub(
        r"\b(is|are|was|were)?\s*not\s+(provided|listed|mentioned|included|"
        r"specified|available|addressed)\s+in\s+the\s+"
        r"(excerpts?|documents?|sources?|context|information provided)\b",
        "is something I don't have", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(according to|based on|from)\s+the\s+"
        r"(excerpts?|documents?|sources?|context|provided information)\b",
        "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bin the (excerpts?|provided (context|information))\b",
                  "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe excerpts?\b", "the information I have",
                  text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    return text.strip()


def _too_similar(candidate: str, question: str, *, threshold: float = 0.7) -> bool:
    """Is the clarification just the resident's own question again?

    Word-overlap (Jaccard) rather than exact match, so trivial rewordings —
    "Where do I report a pothole" vs "Where would I report a pothole?" — are
    still caught.
    """
    def tokens(s: str) -> set[str]:
        return set(re.findall(r"[a-z]+", s.lower())) - {
            "a", "an", "the", "do", "i", "you", "to", "is", "are", "my", "of",
        }

    a, b = tokens(candidate), tokens(question)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


class AnswerGenerator:
    def __init__(self, llm=None):
        self._llm = llm

    @property
    def llm(self):
        return self._llm or registry.llm

    # ------------------------------------------------------------------
    def _messages(
        self, question: str, retrieval: RetrievalResult,
        history: list[dict] | None, *, system: str = SYSTEM_PROMPT,
        department: str | None = None,
    ) -> list[ChatMessage]:
        # Tell the model which department this call was routed to, and how to
        # reach it. Without this it falls back on "contact the appropriate
        # department", which is useless to a caller — the routing layer already
        # knows the answer, so the model should be able to say it.
        if department:
            dept = get_departments().get(department)
            contact = f", phone {dept.phone}" if dept.phone else ""
            system = (
                f"{system}\nDEPARTMENT handling this call: {dept.name}{contact}.\n"
                f"When you cannot answer, name this department and give its "
                f"number."
            )
        messages = [ChatMessage("system", system)]

        for turn in (history or [])[-3:]:
            if turn.get("user"):
                messages.append(ChatMessage("user", turn["user"]))
            if turn.get("assistant"):
                messages.append(ChatMessage("assistant", turn["assistant"]))

        # Four excerpts, not three. Trimming to three dropped the passage that
        # distinguishes garbage days from rubbish day, and the assistant went
        # back to reporting Wednesday for both — the single most important
        # answer in the demo. Correctness outranks the ~0.5s saved.
        context = retrieval.context_block(limit=4, max_chars=3000)
        messages.append(ChatMessage(
            "user",
            f"EXCERPTS FROM VILLAGE INFORMATION:\n{context or '(no relevant excerpts found)'}"
            f"\n\n---\nResident's question: {question}",
        ))
        return messages

    async def generate(
        self, question: str, retrieval: RetrievalResult,
        history: list[dict] | None = None, department: str | None = None,
    ) -> tuple[str, int]:
        response = await self.llm.complete(
            self._messages(question, retrieval, history, department=department),
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        return _clean_for_speech(response.text), response.duration_ms

    async def stream(
        self, question: str, retrieval: RetrievalResult,
        history: list[dict] | None = None, department: str | None = None,
    ) -> AsyncIterator[str]:
        async for piece in self.llm.stream(
            self._messages(question, retrieval, history, department=department),
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        ):
            yield piece

    async def clarify(
        self, question: str, retrieval: RetrievalResult,
        history: list[dict] | None = None, department: str | None = None,
    ) -> tuple[str, int]:
        response = await self.llm.complete(
            self._messages(question, retrieval, history, system=CLARIFY_PROMPT,
                           department=department),
            temperature=0.3, max_tokens=90,
        )
        text = _clean_for_speech(response.text)

        # Guard against the model echoing the question instead of narrowing it.
        # Observed in testing: asked to clarify "Where do I report a pothole?",
        # the model returned that exact sentence, which would leave the caller
        # in a loop.
        if not text or _too_similar(text, question):
            text = _GENERIC_CLARIFY
        return text, response.duration_ms

    # ------------------------------------------------------------------
    @staticmethod
    def refusal(department: str, *, reason: str | None = None,
                emergency: bool = False) -> str:
        """Deterministic refusal text.

        Not model-generated on purpose: when the system is declining to answer,
        the wording must be exact and predictable every single time. Village
        communications staff control it via config/confidence.yaml.
        """
        from app.services.confidence import confidence_engine

        name = get_departments().name_of(department)
        if emergency:
            return confidence_engine.message_for("emergency", department=name) or (
                "If this is an emergency, please hang up and dial 911 right away. "
                f"Otherwise I can connect you with the {name}."
            )
        if reason:
            message = confidence_engine.message_for(
                "policy_restricted", reason=reason, department=name)
            if message:
                return message
        return confidence_engine.message_for("low_confidence", department=name) or (
            "I don't have enough verified information to answer that accurately. "
            f"I can connect you with the {name} so someone there can help."
        )


answer_generator = AnswerGenerator()
