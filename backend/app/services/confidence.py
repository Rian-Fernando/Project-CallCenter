"""Confidence engine (§10, §32) — decides whether to answer, clarify, or refuse.

THE PROBLEM THIS SOLVES
    A language model asked "how confident are you?" will say "very" about
    municipal facts it has never seen. Measured on this exact stack: qwen3:8b
    reported confidence 1.0 while classifying a request it had no grounds to be
    certain about. Self-reported confidence is not evidence.

THE APPROACH
    Combine six signals, five of which the model cannot influence:

      1. top_score            highest retrieval similarity      (vector store)
      2. score_margin         top1 - top3 separation            (vector store)
      3. support_count        how many chunks cleared threshold (corpus)
      4. department_agreement do sources match the routing?     (cross-check)
      5. grounding            is each claim backed by an excerpt? (LLM critic)
      6. policy_restrictions  hard deny-list                    (config)

    Only #5 involves the model, and there it critiques text it has already
    written — a verification task, not introspection. Models are markedly more
    reliable at "is this sentence supported by this passage?" than at "how sure
    am I?".

    Weights and thresholds live in config/confidence.yaml and .env so Village
    staff can tune behavior without touching code.

OUTCOMES
    HIGH   -> answer, with citations
    MEDIUM -> ask one clarifying question
    LOW    -> refuse to guess, escalate to a human department
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any

import yaml

from app.core.config import settings
from app.models.db import ConfidenceLevel
from app.providers.base import ChatMessage
from app.rag.retriever import RetrievalResult

log = logging.getLogger(__name__)


class Action(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ESCALATE = "escalate"


@dataclass
class PolicyHit:
    id: str
    reason: str
    immediate_safety_notice: bool = False


@dataclass
class ConfidenceAssessment:
    score: float
    level: ConfidenceLevel
    action: Action
    signals: dict[str, Any] = field(default_factory=dict)
    policy: PolicyHit | None = None
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "level": self.level.value,
            "action": self.action.value,
            "signals": self.signals,
            "policy_restriction": self.policy.id if self.policy else None,
            "explanation": self.explanation,
        }


@lru_cache
def load_confidence_config() -> dict:
    path = settings.config_dir / "confidence.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log.error("confidence.yaml missing at %s — using conservative defaults.", path)
        # Conservative fallback: retrieval-only, no grounding credit. Errs
        # toward escalation, which is the safe direction.
        return {
            "weights": {"top_score": 0.45, "score_margin": 0.2,
                        "support_count": 0.2, "department_agreement": 0.15},
            "signals": {"top_score": {"floor": 0.3, "ceiling": 0.72},
                        "score_margin": {"floor": 0.0, "ceiling": 0.18},
                        "support_count": {"target": 3}},
            "policy_restrictions": [],
            "messages": {},
        }


def _ramp(value: float, floor: float, ceiling: float) -> float:
    """Map a raw value onto 0..1 between floor and ceiling."""
    if ceiling <= floor:
        return 0.0
    return max(0.0, min(1.0, (value - floor) / (ceiling - floor)))


class ConfidenceEngine:
    def __init__(self, llm=None):
        self._llm = llm

    @property
    def llm(self):
        from app.providers.factory import registry
        return self._llm or registry.llm

    # ------------------------------------------------------------------
    def check_policy(self, text: str) -> PolicyHit | None:
        """Hard safety overrides — these bypass all scoring."""
        low = re.sub(r"\s+", " ", (text or "").lower())
        for rule in load_confidence_config().get("policy_restrictions", []):
            for pattern in rule.get("patterns", []):
                if pattern and pattern in low:
                    return PolicyHit(
                        id=rule.get("id", "restricted"),
                        reason=rule.get("reason", "This requires a person."),
                        immediate_safety_notice=bool(
                            rule.get("immediate_safety_notice", False)),
                    )
        return None

    # ------------------------------------------------------------------
    async def assess(
        self,
        question: str,
        retrieval: RetrievalResult,
        *,
        routing_confidence: float = 0.0,
        routed_department: str | None = None,
        draft_answer: str | None = None,
    ) -> ConfidenceAssessment:
        cfg = load_confidence_config()
        weights = cfg.get("weights", {})
        sig_cfg = cfg.get("signals", {})

        # --- hard policy override ---------------------------------------
        if hit := self.check_policy(question):
            return ConfidenceAssessment(
                score=0.0, level=ConfidenceLevel.LOW, action=Action.ESCALATE,
                signals={"policy_restriction": hit.id, "bypassed_scoring": True},
                policy=hit,
                explanation=f"Policy restriction '{hit.id}': {hit.reason}",
            )

        # --- empty retrieval --------------------------------------------
        if retrieval.is_empty:
            return ConfidenceAssessment(
                score=0.0, level=ConfidenceLevel.LOW, action=Action.ESCALATE,
                signals={"retrieved_chunks": 0},
                explanation="Nothing in the knowledge base matched this question.",
            )

        signals: dict[str, Any] = {}

        # 1. top score
        ts_cfg = sig_cfg.get("top_score", {})
        s_top = _ramp(retrieval.top_score,
                      ts_cfg.get("floor", 0.30), ts_cfg.get("ceiling", 0.72))
        signals["top_score"] = {"raw": round(retrieval.top_score, 4),
                                "normalized": round(s_top, 3)}

        # 2. score margin
        sm_cfg = sig_cfg.get("score_margin", {})
        s_margin = _ramp(retrieval.score_margin,
                         sm_cfg.get("floor", 0.0), sm_cfg.get("ceiling", 0.18))
        signals["score_margin"] = {"raw": round(retrieval.score_margin, 4),
                                   "normalized": round(s_margin, 3)}

        # 3. supporting documents
        target = max(1, sig_cfg.get("support_count", {}).get("target", 3))
        supporting = len(retrieval.above(settings.rag_min_score))
        s_support = min(1.0, supporting / target)
        signals["support_count"] = {"raw": supporting, "target": target,
                                    "normalized": round(s_support, 3)}

        # 4. department agreement
        s_dept = 0.5
        if routed_department:
            depts = retrieval.departments[:4]
            agreeing = sum(1 for d in depts if d == routed_department)
            s_dept = (agreeing / len(depts)) if depts else 0.0
            # Blend in the router's own confidence: a shaky routing decision
            # makes agreement with it less meaningful.
            s_dept = 0.65 * s_dept + 0.35 * min(1.0, routing_confidence)
        signals["department_agreement"] = {
            "routed": routed_department,
            "retrieved": retrieval.departments[:4],
            "normalized": round(s_dept, 3),
        }

        # 5. grounding
        s_ground, ground_meta = await self._grounding_signal(
            question, draft_answer, retrieval, sig_cfg.get("grounding", {}),
        )
        signals["grounding"] = ground_meta

        # --- weighted combination ---------------------------------------
        components = {
            "top_score": s_top,
            "score_margin": s_margin,
            "support_count": s_support,
            "department_agreement": s_dept,
            "grounding": s_ground,
        }
        # Renormalize over signals that actually ran, so skipping the grounding
        # check doesn't silently cap the maximum achievable score.
        active = {k: w for k, w in weights.items()
                  if k in components and components[k] is not None}
        total_weight = sum(active.values()) or 1.0
        score = sum(components[k] * w for k, w in active.items()) / total_weight

        signals["weights_applied"] = {k: round(w / total_weight, 3)
                                      for k, w in active.items()}

        # --- hard override: the draft itself refused ------------------------
        # If the model, looking at the excerpts, concluded it cannot answer,
        # that judgment outranks every retrieval statistic. Strong-looking
        # similarity scores on documents that don't contain the answer are
        # exactly the situation this system exists to catch.
        if ground_meta.get("declined"):
            return ConfidenceAssessment(
                score=round(min(score, settings.confidence_medium - 0.01), 4),
                level=ConfidenceLevel.LOW, action=Action.ESCALATE,
                signals=signals,
                explanation=(
                    "The knowledge base does not contain an answer to this "
                    "question (the drafted response declined to answer)."
                ),
            )

        # --- thresholds ---------------------------------------------------
        if score >= settings.confidence_high:
            level, action = ConfidenceLevel.HIGH, Action.ANSWER
            explanation = "Strong retrieval support and grounded answer."
        elif score >= settings.confidence_medium:
            level, action = ConfidenceLevel.MEDIUM, Action.CLARIFY
            explanation = "Partial match — a clarifying question is needed."
        else:
            level, action = ConfidenceLevel.LOW, Action.ESCALATE
            explanation = "Insufficient verified support to answer safely."

        return ConfidenceAssessment(
            score=round(score, 4), level=level, action=action,
            signals=signals, explanation=explanation,
        )

    # ------------------------------------------------------------------
    async def _grounding_signal(
        self, question: str, draft: str | None,
        retrieval: RetrievalResult, cfg: dict,
    ) -> tuple[float | None, dict]:
        """Ask the model to verify its own draft against the excerpts.

        This is the strongest anti-fabrication signal available, because it
        tests a specific, checkable claim ("is this supported by that text?")
        rather than asking the model to introspect.
        """
        if not settings.grounding_check_enabled or not draft:
            return None, {"checked": False, "reason": "disabled or no draft"}

        verdicts = {
            "supported": cfg.get("supported", 1.0),
            "partial": cfg.get("partial", 0.5),
            "unsupported": cfg.get("unsupported", 0.0),
            # A draft that declines to answer is technically well-grounded —
            # it asserts nothing false. But scoring it as "supported" produced
            # a real failure: a nonsense question got HIGH confidence and was
            # returned as an ANSWER whose content was "I don't have that
            # information." A refusal is evidence the knowledge base LACKS the
            # answer, so it must drive escalation, not confidence.
            "declines": 0.0,
        }
        system = (
            "You verify whether an answer is supported by source excerpts.\n"
            "Reply with ONLY minified JSON:\n"
            '{"verdict":"supported|partial|unsupported|declines",'
            '"unsupported_claims":["..."]}\n\n'
            "supported   = every factual claim appears in the excerpts\n"
            "partial     = the main claim is supported but some details are not\n"
            "unsupported = key claims do not appear in the excerpts at all\n"
            "declines    = the answer does not actually answer the question; it "
            "says the information is unavailable, or only offers to transfer "
            "the caller\n\n"
            "Judge ONLY whether the excerpts contain the claims. Do not use "
            "outside knowledge."
        )
        # Deliberately narrower than the generation context: the critic only
        # needs the passages the answer could plausibly have drawn from, and a
        # smaller prompt roughly halves this second round trip.
        user = (
            f"EXCERPTS:\n{retrieval.context_block(limit=3, max_chars=2200)}\n\n"
            f"QUESTION: {question}\n\nANSWER TO VERIFY:\n{draft}"
        )

        try:
            response = await self.llm.complete(
                [ChatMessage("system", system), ChatMessage("user", user)],
                temperature=0.0, max_tokens=120, json_mode=True,
            )
            from app.routing.router import IntentRouter
            data = IntentRouter._parse_json(response.text)
            verdict = str(data.get("verdict", "")).lower().strip()
            if verdict not in verdicts:
                raise ValueError(f"unrecognized verdict {verdict!r}")
            return verdicts[verdict], {
                "checked": True,
                "verdict": verdict,
                # Signals to the caller that the draft must be replaced by the
                # canonical refusal rather than shown as an answer.
                "declined": verdict == "declines",
                "unsupported_claims": data.get("unsupported_claims", [])[:5],
                "normalized": verdicts[verdict],
            }
        except Exception as exc:
            # A failed check must not be read as success. Use the configured
            # neutral-pessimistic default so the system leans toward escalation.
            default = cfg.get("error_default", 0.35)
            log.warning("Grounding check failed (%s); using default %.2f", exc, default)
            return default, {"checked": False, "error": str(exc)[:120],
                             "normalized": default}

    # ------------------------------------------------------------------
    def message_for(self, key: str, **kwargs) -> str:
        messages = load_confidence_config().get("messages", {})
        template = messages.get(key, "")
        if not template:
            return ""
        try:
            return " ".join(template.format(**kwargs).split())
        except KeyError:
            return " ".join(template.split())


confidence_engine = ConfidenceEngine()
