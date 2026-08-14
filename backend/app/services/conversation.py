"""Conversation orchestrator — one resident turn, end to end.

    text in
      -> session memory (resolve follow-ups)
      -> intent routing
      -> retrieval
      -> draft answer
      -> confidence assessment
      -> answer | clarify | escalate
      -> persist transcript, sources, timings, decision trace

Everything needed to explain a decision afterwards is recorded, because "why
did it refuse?" is a question Village staff will ask, and the answer must not
be "we don't know".
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.database import session_scope
from app.models.db import (
    ChannelType, ConfidenceLevel, Conversation, Escalation, Resolution,
    Turn as TurnRow, UnansweredQuestion,
)
from app.rag.documents import normalize_question
from app.rag.retriever import retriever
from app.routing.departments import GENERAL, get_departments
from app.routing.router import router
from app.services.answering import answer_generator
from app.services.confidence import Action, confidence_engine
from app.services.memory import Session, Turn, session_store
from app.services.contact_lookup import contact_answer
from app.services.smalltalk import (
    SmalltalkMatch, match_smalltalk, match_topic_announcement, topic_reply,
)

log = logging.getLogger(__name__)

# How far above RAG_MIN_SCORE the best match must sit before drafting an answer
# is worth the latency. Below this the turn escalates without an LLM call.
WEAK_RETRIEVAL_MARGIN = 0.12


@dataclass
class TurnResult:
    session_id: str
    conversation_id: str
    turn_id: str
    answer: str
    action: str
    department: str
    department_name: str
    intent: str
    confidence: float
    confidence_level: str
    confidence_signals: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    safety_notice: str | None = None
    timings: dict = field(default_factory=dict)
    used_context: bool = False

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "answer": self.answer,
            "action": self.action,
            "department": self.department,
            "department_name": self.department_name,
            "intent": self.intent,
            "confidence": round(self.confidence, 3),
            "confidence_level": self.confidence_level,
            "confidence_signals": self.confidence_signals,
            "routing": self.routing,
            "sources": self.sources,
            "escalation": self.escalation,
            "safety_notice": self.safety_notice,
            "timings": self.timings,
            "used_conversation_context": self.used_context,
        }


class ConversationService:
    async def handle(
        self,
        session_id: str,
        text: str,
        *,
        channel: str = ChannelType.BROWSER.value,
        stt_ms: int | None = None,
    ) -> TurnResult:
        started = time.perf_counter()
        timings: dict[str, int] = {}
        if stt_ms is not None:
            timings["stt_ms"] = stt_ms

        session = session_store.get_or_create(session_id, channel=channel)
        question = (text or "").strip()
        history = session.history_dicts()

        # Greetings and pleasantries are conversation, not requests. Without
        # this, "hello" ran the full pipeline, retrieved nothing relevant,
        # escalated to a department, and filed itself in the human review
        # queue — noise that would bury real knowledge gaps.
        if (smalltalk := match_smalltalk(question, has_history=bool(session.turns))):
            return await self._smalltalk_turn(
                session, question, smalltalk, timings, started,
            )

        # --- resolve follow-ups ------------------------------------------
        used_context = session.needs_context(question)
        retrieval_query = session.contextual_query(question)

        # --- route --------------------------------------------------------
        t0 = time.perf_counter()
        decision = await router.classify(question, history=history)
        timings["routing_ms"] = int((time.perf_counter() - t0) * 1000)

        # A bare follow-up ("when is mine?") often has no routable keywords of
        # its own; inherit the session's department rather than dropping to
        # `general` and losing the thread.
        if used_context and decision.confidence < 0.5 and session.current_department:
            decision.department = session.current_department
            decision.method = f"{decision.method}+session_context"

        # The caller named a topic but hasn't asked anything yet. Answer with a
        # short invitation rather than running retrieval on a non-question —
        # which previously produced a refusal and a spurious review-queue entry
        # for the opening line of a normal call.
        if (topic := match_topic_announcement(question)) is not None:
            return await self._topic_turn(
                session, question, topic, decision, timings, started,
            )

        # "What's the number for Public Works?" has one right answer and it is
        # already configured. Sending it through retrieval let a mediocre
        # similarity score downgrade a known fact into a clarifying question.
        if (contact := contact_answer(question, decision.department)) is not None:
            return await self._direct_turn(
                session, question, contact, decision, timings, started,
                intent_suffix="contact_lookup",
            )

        # --- retrieve ------------------------------------------------------
        t0 = time.perf_counter()
        retrieval = await retriever.retrieve(
            retrieval_query, department=decision.department,
        )
        timings["retrieval_ms"] = int((time.perf_counter() - t0) * 1000)

        # --- draft ---------------------------------------------------------
        # The draft is generated before scoring because the grounding signal
        # needs something concrete to verify. If confidence then comes back
        # low, the draft is discarded and replaced by a refusal — an unverified
        # draft is never shown to the resident.
        draft, llm_ms = "", 0
        policy_hit = confidence_engine.check_policy(question)

        # Skip drafting entirely when retrieval is so weak that no draft could
        # survive scoring. This is the escalation fast path: it avoids two LLM
        # round trips (~8s) on questions the knowledge base plainly cannot
        # answer, which is exactly when a caller least wants to wait.
        hopeless = (
            retrieval.is_empty
            or retrieval.top_score < (settings.rag_min_score + WEAK_RETRIEVAL_MARGIN)
        )

        if not policy_hit and not hopeless:
            try:
                draft, llm_ms = await answer_generator.generate(
                    question, retrieval, history,
                    department=decision.department,
                )
            except Exception as exc:
                log.error("Answer generation failed: %s", exc)
        timings["llm_ms"] = llm_ms

        # --- assess ---------------------------------------------------------
        t0 = time.perf_counter()
        assessment = await confidence_engine.assess(
            question, retrieval,
            routing_confidence=decision.confidence,
            routed_department=decision.department,
            draft_answer=draft or None,
        )
        timings["confidence_ms"] = int((time.perf_counter() - t0) * 1000)

        # --- decide ----------------------------------------------------------
        safety_notice = None
        escalation_payload = None

        if assessment.action is Action.ANSWER and draft:
            answer = draft
        elif assessment.action is Action.CLARIFY:
            try:
                answer, extra_ms = await answer_generator.clarify(
                    question, retrieval, history,
                    department=decision.department,
                )
                timings["llm_ms"] = timings.get("llm_ms", 0) + extra_ms
            except Exception as exc:
                log.error("Clarification failed: %s", exc)
                answer = answer_generator.refusal(decision.department)
                assessment.action = Action.ESCALATE
        else:
            emergency = bool(assessment.policy and assessment.policy.immediate_safety_notice)
            answer = answer_generator.refusal(
                decision.department,
                reason=assessment.policy.reason if assessment.policy else None,
                emergency=emergency,
            )
            if emergency:
                safety_notice = "For emergencies, call 911."

        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        # --- persist ----------------------------------------------------------
        sources = retrieval.sources()
        turn = Turn(
            user=question, assistant=answer,
            department=decision.department, intent=decision.intent,
            confidence=assessment.score, action=assessment.action.value,
            sources=sources,
        )
        session.add_turn(turn)

        conversation_id, turn_id = await self._persist(
            session, turn, decision, assessment, retrieval, sources, timings,
        )

        if assessment.action is Action.ESCALATE:
            escalation_payload = await self._escalate(
                session, conversation_id, turn_id, question,
                decision, assessment, retrieval,
            )

        return TurnResult(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            answer=answer,
            action=assessment.action.value,
            department=decision.department,
            department_name=get_departments().name_of(decision.department),
            intent=decision.intent,
            confidence=assessment.score,
            confidence_level=assessment.level.value,
            confidence_signals=assessment.signals,
            routing=decision.as_dict(),
            sources=sources,
            escalation=escalation_payload,
            safety_notice=safety_notice,
            timings=timings,
            used_context=used_context,
        )

    # ------------------------------------------------------------------
    async def handle_streaming(
        self, session_id: str, text: str,
        *, channel: str = ChannelType.BROWSER.value,
    ) -> AsyncIterator[dict]:
        """Stream a turn as Server-Sent Events.

        THE ORDERING PROBLEM
            Safety says: verify before speaking. Latency says: speak as soon as
            possible. These conflict, and resolving it honestly matters.

            The resolution is to decide *whether* answering is permissible using
            the signals available before generation — policy restrictions and
            retrieval strength — and only stream when those already clear the
            bar. The grounding critic then runs on the completed text and is
            reported in the `done` event.

            If grounding comes back bad on text already spoken, we cannot unsay
            it, so the event carries `grounding_failed` and the UI shows a
            correction notice. Weak-retrieval turns never stream at all: they
            escalate, and the caller hears only the refusal.
        """
        started = time.perf_counter()
        timings: dict[str, int] = {}

        session = session_store.get_or_create(session_id, channel=channel)
        question = (text or "").strip()
        history = session.history_dicts()

        if smalltalk := match_smalltalk(question, has_history=bool(session.turns)):
            result = await self._smalltalk_turn(
                session, question, smalltalk, timings, started,
            )
            yield {"type": "meta", "data": {
                "session_id": session_id, "action": "answer",
                "department": result.department,
                "department_name": result.department_name,
                "smalltalk": True, "sources": [],
            }}
            yield {"type": "delta", "data": {"text": result.answer}}
            yield {"type": "done", "data": result.as_dict()}
            return

        used_context = session.needs_context(question)
        retrieval_query = session.contextual_query(question)

        t0 = time.perf_counter()
        decision = await router.classify(question, history=history)
        timings["routing_ms"] = int((time.perf_counter() - t0) * 1000)
        if used_context and decision.confidence < 0.5 and session.current_department:
            decision.department = session.current_department
            decision.method = f"{decision.method}+session_context"

        if (contact := contact_answer(question, decision.department)) is not None:
            result = await self._direct_turn(
                session, question, contact, decision, timings, started,
                intent_suffix="contact_lookup",
            )
            yield {"type": "meta", "data": {
                "session_id": session_id, "department": result.department,
                "department_name": result.department_name,
                "sources": [], "will_stream": False,
            }}
            yield {"type": "delta", "data": {"text": result.answer}}
            yield {"type": "done", "data": result.as_dict()}
            return

        if (topic := match_topic_announcement(question)) is not None:
            result = await self._topic_turn(
                session, question, topic, decision, timings, started,
            )
            yield {"type": "meta", "data": {
                "session_id": session_id, "department": result.department,
                "department_name": result.department_name,
                "sources": [], "will_stream": False,
            }}
            yield {"type": "delta", "data": {"text": result.answer}}
            yield {"type": "done", "data": result.as_dict()}
            return

        t0 = time.perf_counter()
        retrieval = await retriever.retrieve(
            retrieval_query, department=decision.department,
        )
        timings["retrieval_ms"] = int((time.perf_counter() - t0) * 1000)

        policy_hit = confidence_engine.check_policy(question)
        hopeless = (
            retrieval.is_empty
            or retrieval.top_score < (settings.rag_min_score + WEAK_RETRIEVAL_MARGIN)
        )
        sources = retrieval.sources()

        yield {"type": "meta", "data": {
            "session_id": session_id,
            "department": decision.department,
            "department_name": get_departments().name_of(decision.department),
            "intent": decision.intent,
            "routing": decision.as_dict(),
            "sources": sources,
            "will_stream": not (policy_hit or hopeless),
        }}

        # --- non-streaming paths: refuse without speaking a draft ----------
        if policy_hit or hopeless:
            result = await self.handle(session_id, question, channel=channel)
            yield {"type": "delta", "data": {"text": result.answer}}
            yield {"type": "done", "data": result.as_dict()}
            return

        # --- stream the draft ------------------------------------------------
        t0 = time.perf_counter()
        pieces: list[str] = []
        try:
            async for piece in answer_generator.stream(
                question, retrieval, history, department=decision.department,
            ):
                pieces.append(piece)
                yield {"type": "delta", "data": {"text": piece}}
        except Exception as exc:
            log.error("Streaming generation failed: %s", exc)
            fallback = answer_generator.refusal(decision.department)
            yield {"type": "delta", "data": {"text": fallback}}
            pieces = [fallback]
        timings["llm_ms"] = int((time.perf_counter() - t0) * 1000)

        from app.services.answering import _clean_for_speech
        answer = _clean_for_speech("".join(pieces))

        # --- verify after the fact -------------------------------------------
        t0 = time.perf_counter()
        assessment = await confidence_engine.assess(
            question, retrieval,
            routing_confidence=decision.confidence,
            routed_department=decision.department,
            draft_answer=answer or None,
        )
        timings["confidence_ms"] = int((time.perf_counter() - t0) * 1000)
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        grounding_failed = assessment.action is not Action.ANSWER

        turn = Turn(
            user=question, assistant=answer,
            department=decision.department, intent=decision.intent,
            confidence=assessment.score, action=assessment.action.value,
            sources=sources,
        )
        session.add_turn(turn)
        conversation_id, turn_id = await self._persist(
            session, turn, decision, assessment, retrieval, sources, timings,
        )

        escalation_payload = None
        if assessment.action is Action.ESCALATE:
            escalation_payload = await self._escalate(
                session, conversation_id, turn_id, question,
                decision, assessment, retrieval,
            )

        result = TurnResult(
            session_id=session_id, conversation_id=conversation_id,
            turn_id=turn_id, answer=answer,
            action=assessment.action.value,
            department=decision.department,
            department_name=get_departments().name_of(decision.department),
            intent=decision.intent, confidence=assessment.score,
            confidence_level=assessment.level.value,
            confidence_signals=assessment.signals,
            routing=decision.as_dict(), sources=sources,
            escalation=escalation_payload, timings=timings,
            used_context=used_context,
        )
        payload = result.as_dict()
        # Tells the UI that spoken text failed post-hoc verification and a
        # correction should be shown.
        payload["grounding_failed"] = grounding_failed
        yield {"type": "done", "data": payload}

    # ------------------------------------------------------------------
    async def _direct_turn(
        self, session: Session, question: str, answer: str,
        decision, timings: dict, started: float,
        *, intent_suffix: str, action: str = Action.ANSWER.value,
    ) -> TurnResult:
        """Persist a deterministic answer that bypassed retrieval.

        Used where the correct response is already known — a configured
        department phone number, for instance — so no similarity score can
        turn it into a hedge.
        """
        department_name = get_departments().name_of(decision.department)
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        turn = Turn(
            user=question, assistant=answer,
            department=decision.department,
            intent=f"{decision.department}_{intent_suffix}",
            confidence=1.0, action=action, sources=[],
        )
        session.add_turn(turn)

        async with session_scope() as db:
            conversation = None
            if session.conversation_id:
                conversation = await db.get(Conversation, session.conversation_id)
            if conversation is None:
                conversation = Conversation(
                    session_id=session.session_id, channel=session.channel,
                )
                db.add(conversation)
                await db.flush()
                session.conversation_id = conversation.id

            turn_row = TurnRow(
                conversation_id=conversation.id,
                turn_index=len(session.turns) - 1,
                user_text=question, assistant_text=answer,
                department=decision.department, intent=turn.intent,
                routing_method=decision.method,
                routing_confidence=decision.confidence,
                confidence_score=1.0,
                confidence_level=ConfidenceLevel.HIGH.value,
                confidence_signals={"deterministic": intent_suffix,
                                    "pipeline_skipped": True},
                sources=[], retrieved_count=0,
                response_ms=timings["total_ms"], action=action,
            )
            db.add(turn_row)
            await db.flush()
            conversation.turn_count = len(session.turns)
            conversation.primary_department = decision.department
            conversation_id, turn_id = conversation.id, turn_row.id

        return TurnResult(
            session_id=session.session_id,
            conversation_id=conversation_id, turn_id=turn_id,
            answer=answer, action=action,
            department=decision.department, department_name=department_name,
            intent=turn.intent, confidence=1.0,
            confidence_level=ConfidenceLevel.HIGH.value,
            confidence_signals={"deterministic": intent_suffix,
                                "pipeline_skipped": True},
            routing=decision.as_dict(), sources=[], timings=timings,
        )

    # ------------------------------------------------------------------
    async def _topic_turn(
        self, session: Session, question: str, topic: str,
        decision, timings: dict, started: float,
    ) -> TurnResult:
        """Respond to a topic announcement with a clarifying invitation.

        Records the department on the session so the caller's real question —
        which is usually a bare follow-up like "when is mine?" — routes
        correctly on the next turn.
        """
        department_name = get_departments().name_of(decision.department)
        reply = topic_reply(topic, department_name)
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        turn = Turn(
            user=question, assistant=reply,
            department=decision.department,
            intent=f"{decision.department}_topic_announcement",
            confidence=1.0, action=Action.CLARIFY.value, sources=[],
        )
        session.add_turn(turn)

        async with session_scope() as db:
            conversation = None
            if session.conversation_id:
                conversation = await db.get(Conversation, session.conversation_id)
            if conversation is None:
                conversation = Conversation(
                    session_id=session.session_id, channel=session.channel,
                )
                db.add(conversation)
                await db.flush()
                session.conversation_id = conversation.id

            turn_row = TurnRow(
                conversation_id=conversation.id,
                turn_index=len(session.turns) - 1,
                user_text=question, assistant_text=reply,
                department=decision.department, intent=turn.intent,
                routing_method=decision.method,
                routing_confidence=decision.confidence,
                confidence_score=1.0,
                confidence_level=ConfidenceLevel.HIGH.value,
                confidence_signals={"topic_announcement": topic,
                                    "pipeline_skipped": True},
                sources=[], retrieved_count=0,
                response_ms=timings["total_ms"],
                action=Action.CLARIFY.value,
            )
            db.add(turn_row)
            await db.flush()
            conversation.turn_count = len(session.turns)
            conversation.primary_department = decision.department
            if not conversation.escalated:
                conversation.resolution = Resolution.CLARIFYING.value
            conversation_id, turn_id = conversation.id, turn_row.id

        return TurnResult(
            session_id=session.session_id,
            conversation_id=conversation_id, turn_id=turn_id,
            answer=reply, action=Action.CLARIFY.value,
            department=decision.department, department_name=department_name,
            intent=turn.intent, confidence=1.0,
            confidence_level=ConfidenceLevel.HIGH.value,
            confidence_signals={"topic_announcement": topic,
                                "pipeline_skipped": True},
            routing=decision.as_dict(), sources=[], timings=timings,
        )

    # ------------------------------------------------------------------
    async def _smalltalk_turn(
        self, session: Session, question: str, smalltalk: SmalltalkMatch,
        timings: dict, started: float,
    ) -> TurnResult:
        """Handle a greeting/thanks/goodbye without retrieval or a model call."""
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        department = session.current_department or GENERAL
        turn = Turn(
            user=question, assistant=smalltalk.reply,
            department=department, intent=f"smalltalk_{smalltalk.kind}",
            confidence=1.0, action=Action.ANSWER.value, sources=[],
        )
        session.add_turn(turn)

        async with session_scope() as db:
            conversation = None
            if session.conversation_id:
                conversation = await db.get(Conversation, session.conversation_id)
            if conversation is None:
                conversation = Conversation(
                    session_id=session.session_id, channel=session.channel,
                )
                db.add(conversation)
                await db.flush()
                session.conversation_id = conversation.id

            turn_row = TurnRow(
                conversation_id=conversation.id,
                turn_index=len(session.turns) - 1,
                user_text=question, assistant_text=smalltalk.reply,
                department=department, intent=turn.intent,
                routing_method="smalltalk", routing_confidence=1.0,
                confidence_score=1.0,
                confidence_level=ConfidenceLevel.HIGH.value,
                confidence_signals={"smalltalk": smalltalk.kind},
                sources=[], retrieved_count=0,
                response_ms=timings["total_ms"],
                action=Action.ANSWER.value,
            )
            db.add(turn_row)
            await db.flush()
            conversation.turn_count = len(session.turns)
            if smalltalk.ends_call:
                conversation.ended_at = datetime.now(timezone.utc)
            conversation_id, turn_id = conversation.id, turn_row.id

        return TurnResult(
            session_id=session.session_id,
            conversation_id=conversation_id, turn_id=turn_id,
            answer=smalltalk.reply, action=Action.ANSWER.value,
            department=department,
            department_name=get_departments().name_of(department),
            intent=turn.intent, confidence=1.0,
            confidence_level=ConfidenceLevel.HIGH.value,
            confidence_signals={"smalltalk": smalltalk.kind,
                                "pipeline_skipped": True},
            routing={"method": "smalltalk", "department": department,
                     "confidence": 1.0},
            sources=[], timings=timings,
        )

    # ------------------------------------------------------------------
    async def _persist(
        self, session: Session, turn: Turn, decision, assessment,
        retrieval, sources: list[dict], timings: dict,
    ) -> tuple[str, str]:
        async with session_scope() as db:
            conversation = None
            if session.conversation_id:
                conversation = await db.get(Conversation, session.conversation_id)
            if conversation is None:
                conversation = Conversation(
                    session_id=session.session_id, channel=session.channel,
                )
                db.add(conversation)
                await db.flush()
                session.conversation_id = conversation.id

            turn_row = TurnRow(
                conversation_id=conversation.id,
                turn_index=len(session.turns) - 1,
                user_text=turn.user,
                assistant_text=turn.assistant,
                department=decision.department,
                intent=decision.intent,
                routing_method=decision.method,
                routing_confidence=decision.confidence,
                confidence_score=assessment.score,
                confidence_level=assessment.level.value,
                confidence_signals=assessment.signals,
                policy_restriction=assessment.policy.id if assessment.policy else None,
                sources=sources,
                retrieved_count=len(retrieval.chunks),
                response_ms=timings.get("total_ms"),
                stt_ms=timings.get("stt_ms"),
                retrieval_ms=timings.get("retrieval_ms"),
                llm_ms=timings.get("llm_ms"),
                action=assessment.action.value,
            )
            db.add(turn_row)
            await db.flush()

            # Roll up conversation-level fields for the dashboard.
            conversation.turn_count = len(session.turns)
            conversation.primary_department = decision.department
            conversation.primary_intent = decision.intent
            if assessment.action is Action.ESCALATE:
                conversation.escalated = True
                conversation.resolution = Resolution.ESCALATED.value
            elif assessment.action is Action.CLARIFY:
                if not conversation.escalated:
                    conversation.resolution = Resolution.CLARIFYING.value
            elif not conversation.escalated:
                conversation.resolution = Resolution.AI_RESOLVED.value

            durations = [
                t.response_ms for t in (await db.execute(
                    select(TurnRow).where(TurnRow.conversation_id == conversation.id)
                )).scalars() if t.response_ms
            ]
            if durations:
                conversation.avg_response_ms = sum(durations) / len(durations)

            return conversation.id, turn_row.id

    # ------------------------------------------------------------------
    async def _escalate(
        self, session: Session, conversation_id: str, turn_id: str,
        question: str, decision, assessment, retrieval,
    ) -> dict:
        """Record an escalation and queue the question for human review (§14)."""
        name = get_departments().name_of(decision.department)

        if assessment.policy:
            reason_code = assessment.policy.id
            reason = assessment.policy.reason
        elif retrieval.is_empty:
            reason_code = "no_matching_sources"
            reason = "No documents in the knowledge base matched this question."
        else:
            reason_code = "low_confidence"
            reason = (
                f"Retrieved sources were too weak to answer safely "
                f"(confidence {assessment.score:.2f}, "
                f"best match {retrieval.top_score:.2f})."
            )

        summary = self._summarize(session)
        recommended = (
            f"Confirm the resident's question and provide the verified "
            f"{name} answer. If this question recurs, add it to the knowledge "
            f"base via the admin review queue."
        )

        async with session_scope() as db:
            escalation = Escalation(
                conversation_id=conversation_id, turn_id=turn_id,
                department=decision.department,
                reason=reason, reason_code=reason_code,
                caller_question=question,
                conversation_summary=summary,
                recommended_action=recommended,
                confidence_score=assessment.score,
                simulated=True,
            )
            db.add(escalation)

            # Queue for review, collapsing repeats of the same question so the
            # dashboard shows demand rather than duplicates.
            normalized = normalize_question(question)
            existing = (await db.execute(
                select(UnansweredQuestion).where(
                    UnansweredQuestion.normalized_question == normalized,
                    UnansweredQuestion.status.in_(["needs_review", "in_review"]),
                )
            )).scalars().first()

            if existing:
                existing.occurrence_count += 1
                existing.last_asked_at = datetime.now(timezone.utc)
            else:
                db.add(UnansweredQuestion(
                    question=question,
                    normalized_question=normalized,
                    conversation_id=conversation_id,
                    transcript=session.transcript(),
                    detected_department=decision.department,
                    attempted_sources=retrieval.sources(limit=5),
                    confidence_score=assessment.score,
                    confidence_signals=assessment.signals,
                    outcome=Resolution.ESCALATED.value,
                ))

            await db.flush()
            return {
                "id": escalation.id,
                "department": decision.department,
                "department_name": name,
                "reason": reason,
                "reason_code": reason_code,
                "caller_question": question,
                "conversation_summary": summary,
                "recommended_action": recommended,
                "confidence": round(assessment.score, 3),
                "simulated": True,
                "transcript": session.transcript(),
            }

    @staticmethod
    def _summarize(session: Session) -> str:
        """Plain-text summary for the human receiving the transfer.

        Built by concatenation rather than by the model: a transfer summary
        must be a faithful record of what was said, and a generated paraphrase
        can drift from it.
        """
        if not session.turns:
            return "No conversation recorded."
        parts = [
            f"Resident asked: {t.user}" + (
                f" | Assistant: {t.assistant[:140]}" if t.assistant else ""
            )
            for t in session.turns[-4:]
        ]
        return " || ".join(parts)[:2000]


conversation_service = ConversationService()
