"""Intent router — decides which department owns a resident's request (§9).

TWO-STAGE DESIGN, rules first:

  Stage 1  Deterministic rules (instant, free, fully auditable)
           - exact override phrases: "building permit" -> building
           - weighted keyword scoring from departments.yaml
           A confident, unambiguous rule match is returned immediately.

  Stage 2  LLM classification, only when the rules are unclear
           - no keyword matched at all, or
           - the top two departments are within a small margin

Why rules first? Most municipal calls are lexically obvious ("my garbage wasn't
picked up"), and answering them without a model call keeps voice latency near
zero and behavior explainable to Village staff. The LLM handles the genuinely
ambiguous remainder, which is where it actually adds value.

The router deliberately DISCARDS the model's self-reported confidence and
computes its own from the margin between candidates. Measured on this stack,
qwen3:8b returned `"confidence": 1.0` on a routing task where it had no basis
for certainty; self-assessment is not a usable signal.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.providers.base import ChatMessage
from app.providers.factory import registry
from app.routing.departments import GENERAL, get_departments

log = logging.getLogger(__name__)

# Rule-match margins.
RULES_CONFIDENT_MARGIN = 1.5   # top beats runner-up by this -> trust the rules
RULES_MIN_SCORE = 1.0          # below this, treat as "no signal"


@dataclass
class RoutingDecision:
    department: str = GENERAL
    intent: str = "general_inquiry"
    confidence: float = 0.0
    method: str = "rules"              # rules | llm | hybrid | fallback
    requires_human: bool = False
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    duration_ms: int = 0
    reasoning: str = ""

    def as_dict(self) -> dict:
        return {
            "department": self.department,
            "department_name": get_departments().name_of(self.department),
            "intent": self.intent,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "requires_human": self.requires_human,
            "alternatives": [
                {"department": d, "score": round(s, 3)} for d, s in self.alternatives[:3]
            ],
        }


class IntentRouter:
    def __init__(self, llm=None):
        self._llm = llm
        self.departments = get_departments()

    @property
    def llm(self):
        return self._llm or registry.llm

    # ------------------------------------------------------------------
    async def classify(
        self, text: str, *, history: list[dict] | None = None,
        allow_llm: bool = True,
    ) -> RoutingDecision:
        started = time.perf_counter()
        query = (text or "").strip()

        if not query:
            return RoutingDecision(
                department=GENERAL, intent="empty", confidence=0.0,
                method="fallback", requires_human=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        # Follow-ups like "when is mine?" carry no routable keywords of their
        # own; fold in recent turns so context decides the department.
        routing_text = self._with_context(query, history)

        decision = self._classify_by_rules(routing_text, original=query)

        if allow_llm and self._needs_llm(decision):
            try:
                llm_decision = await self._classify_by_llm(query, history)
                decision = self._combine(decision, llm_decision)
            except Exception as exc:
                # A model failure must never block routing; the rule result
                # (even if weak) is better than no answer.
                log.warning("LLM routing failed, keeping rule result: %s", exc)
                decision.method = "rules"
                decision.reasoning = f"LLM unavailable ({type(exc).__name__})"

        decision.duration_ms = int((time.perf_counter() - started) * 1000)
        return decision

    # ------------------------------------------------------------------
    @staticmethod
    def _with_context(query: str, history: list[dict] | None) -> str:
        if not history:
            return query
        recent = " ".join(
            turn.get("user", "") for turn in history[-2:] if turn.get("user")
        )
        return f"{recent} {query}".strip() if recent else query

    def _classify_by_rules(self, text: str, *, original: str) -> RoutingDecision:
        # An explicit override phrase is decisive by construction.
        if forced := self.departments.check_overrides(text):
            return RoutingDecision(
                department=forced,
                intent=self._infer_intent(original, forced),
                confidence=0.95, method="rules",
                reasoning="matched an explicit disambiguation phrase",
            )

        scores = self.departments.score_text(text)
        if not scores:
            return RoutingDecision(
                department=GENERAL, intent="general_inquiry", confidence=0.0,
                method="rules", reasoning="no keyword matched",
            )

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top_dept, top_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top_score - runner_up

        # Confidence is derived from separation between candidates, not from
        # absolute score: a clear winner is what matters.
        if top_score < RULES_MIN_SCORE:
            confidence = 0.25
        elif len(ranked) == 1:
            # Exactly one department matched and nothing competed with it.
            # "pothole" is a single keyword but it is unambiguous, and the old
            # margin-only formula scored it 0.47 — low enough to trigger a
            # needless LLM call on the most common kind of municipal request.
            confidence = min(0.90, 0.72 + 0.06 * top_score)
        elif margin >= RULES_CONFIDENT_MARGIN:
            confidence = min(0.93, 0.70 + 0.05 * margin)
        else:
            confidence = min(0.62, 0.35 + 0.12 * margin)

        return RoutingDecision(
            department=top_dept,
            intent=self._infer_intent(original, top_dept),
            confidence=confidence, method="rules",
            alternatives=ranked[:4],
            reasoning=f"keyword score {top_score:.2f}, margin {margin:.2f}",
        )

    @staticmethod
    def _needs_llm(decision: RoutingDecision) -> bool:
        return decision.confidence < 0.66

    async def _classify_by_llm(
        self, query: str, history: list[dict] | None,
    ) -> RoutingDecision:
        catalog = "\n".join(
            f"- {d.id}: {' '.join(d.description.split())}"
            for d in self.departments.all()
        )
        context = ""
        if history:
            lines = [
                f"{'Resident' if k == 'user' else 'Assistant'}: {v}"
                for turn in history[-3:] for k, v in turn.items()
                if k in ("user", "assistant") and v
            ]
            if lines:
                context = "Earlier in this call:\n" + "\n".join(lines) + "\n\n"

        system = (
            "You route calls for a municipal village hall. Choose the single "
            "department best able to handle the caller's request.\n\n"
            f"DEPARTMENTS:\n{catalog}\n\n"
            "Reply with ONLY minified JSON, no prose:\n"
            '{"department":"<id>","intent":"<short_snake_case_intent>",'
            '"ambiguous":<true|false>}\n\n'
            "Set \"ambiguous\" to true if the request could reasonably belong "
            "to more than one department, or if it is too vague to route. "
            "Use \"general\" only when nothing else fits."
        )
        user = f"{context}Caller: {query}"

        response = await self.llm.complete(
            [ChatMessage("system", system), ChatMessage("user", user)],
            temperature=0.0, max_tokens=120,
            model=settings.router_model, json_mode=True,
        )

        data = self._parse_json(response.text)
        department = data.get("department", GENERAL)
        if not self.departments.exists(department):
            department = GENERAL

        ambiguous = bool(data.get("ambiguous", False))
        intent = str(data.get("intent") or "general_inquiry")[:80]
        intent = re.sub(r"[^a-z0-9_]+", "_", intent.lower()).strip("_") or "general_inquiry"

        return RoutingDecision(
            department=department,
            intent=intent,
            # Note: the model's own "confidence" field is deliberately not
            # requested and not used. `ambiguous` is a behavioral question the
            # model answers far more reliably than numeric self-assessment.
            confidence=0.45 if ambiguous else 0.78,
            method="llm",
            requires_human=ambiguous and department == GENERAL,
            reasoning="model classification" + (" (flagged ambiguous)" if ambiguous else ""),
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = (text or "").strip()
        # Models occasionally wrap JSON in code fences despite instructions.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if match := re.search(r"\{.*\}", text, re.DOTALL):
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        log.debug("Unparseable router output: %.200s", text)
        return {}

    @staticmethod
    def _combine(rules: RoutingDecision, llm: RoutingDecision) -> RoutingDecision:
        """Merge a weak rule result with the model's opinion.

        Agreement is strong evidence: two independent methods reaching the same
        department earns a confidence above either one alone.
        """
        if rules.department == llm.department and rules.confidence > 0:
            return RoutingDecision(
                department=rules.department,
                intent=llm.intent or rules.intent,
                confidence=min(0.95, max(rules.confidence, llm.confidence) + 0.12),
                method="hybrid",
                alternatives=rules.alternatives,
                requires_human=False,
                reasoning="rules and model agree",
            )
        llm.alternatives = rules.alternatives
        llm.reasoning = f"model overrode weak rule match ({rules.reasoning})"
        return llm

    def _infer_intent(self, text: str, department: str) -> str:
        """Derive a coarse intent label for analytics.

        Intentionally simple and deterministic — this drives dashboard grouping,
        not behavior, so a model call would be wasted latency.
        """
        low = text.lower()
        verbs = [
            (r"\b(report|broken|damaged|not working|out|missing|complain)\b", "report_issue"),
            (r"\b(when|what time|schedule|hours|open|close)\b", "schedule_inquiry"),
            (r"\b(how much|cost|fee|price|pay|bill|owe)\b", "payment_inquiry"),
            (r"\b(apply|application|register|sign up|permit|license)\b", "application_inquiry"),
            (r"\b(where|location|address|directions)\b", "location_inquiry"),
            (r"\b(can i|am i allowed|is it legal|do i need)\b", "eligibility_inquiry"),
            (r"\b(reserve|reservation|book|rent)\b", "reservation_inquiry"),
            (r"\b(who|contact|phone|call|speak|talk)\b", "contact_inquiry"),
        ]
        for pattern, label in verbs:
            if re.search(pattern, low):
                return f"{department}_{label}" if department != GENERAL else label
        return f"{department}_inquiry" if department != GENERAL else "general_inquiry"


router = IntentRouter()
