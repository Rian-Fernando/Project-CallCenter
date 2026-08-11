"""Conversation memory (§12) — context within a single call, and nothing more.

PRIVACY BOUNDARY (§17): memory is scoped to one session and held in process.
There is no caller profile, no cross-session history, and no identity linkage.
A session id is a random UUID; when the session ends or expires, its context is
gone. Persistent transcripts live in the database under the retention policy and
are never used to build a profile.

WHAT THIS ENABLES
    Resident: "I have a question about garbage pickup."
    Assistant: "Sure — what would you like to know?"
    Resident: "When is mine?"
                     ^^^^ resolved against the session topic, not a user record
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

MAX_TURNS = 20
SESSION_TTL_SECONDS = 60 * 60          # 1 hour of inactivity
CONTEXT_TURNS = 6                      # turns fed to the model

# Utterances that cannot stand alone and need prior context to make sense.
_REFERENTIAL = re.compile(
    r"\b(it|that|this|those|these|mine|ours|there|then|they|them|"
    r"the same|him|her|he|she)\b",
    re.IGNORECASE,
)
_SHORT_FOLLOWUP = re.compile(
    r"^\s*(and|but|so|what about|how about|ok|okay|yes|no|yeah|"
    r"when|where|how|why|who|which|what)\b",
    re.IGNORECASE,
)
# Above this length, a leading question word is just normal phrasing, not a
# continuation of the previous turn.
_FOLLOWUP_WORD_LIMIT = 9


@dataclass
class Turn:
    user: str
    assistant: str = ""
    department: str | None = None
    intent: str | None = None
    confidence: float | None = None
    action: str | None = None
    sources: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class Session:
    session_id: str
    conversation_id: str | None = None
    channel: str = "browser"
    turns: list[Turn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # Topic carried forward so "when is mine?" still routes to Sanitation.
    current_department: str | None = None
    current_topic: str | None = None
    pending_clarification: str | None = None

    def touch(self) -> None:
        self.last_active = time.time()

    @property
    def expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS:]
        if turn.department:
            self.current_department = turn.department
        if turn.user:
            self.current_topic = turn.user[:200]
        self.touch()

    # -- context shaping ----------------------------------------------
    def history_dicts(self, limit: int = CONTEXT_TURNS) -> list[dict]:
        return [
            {"user": t.user, "assistant": t.assistant}
            for t in self.turns[-limit:]
        ]

    def transcript(self) -> list[dict]:
        return [
            {
                "user": t.user,
                "assistant": t.assistant,
                "department": t.department,
                "confidence": t.confidence,
                "action": t.action,
            }
            for t in self.turns
        ]

    def needs_context(self, text: str) -> bool:
        """Does this utterance depend on what came before?

        Used to decide whether to rewrite a follow-up into a standalone query
        before retrieval — "when is mine?" retrieves nothing on its own.
        """
        if not self.turns:
            return False
        stripped = (text or "").strip()
        words = len(stripped.split())

        # Very short utterances are almost always follow-ups ("When?", "Mine?").
        if words <= 6:
            return True

        # A pronoun anywhere means something earlier is being referred to.
        if _REFERENTIAL.search(stripped):
            return True

        # A leading question word only signals a follow-up on a SHORT utterance.
        # Applying it to any length misclassified fully self-contained questions
        # like "How do I apply for a building permit for a new deck?" as
        # follow-ups, which polluted the retrieval query with the prior topic.
        return words <= _FOLLOWUP_WORD_LIMIT and bool(_SHORT_FOLLOWUP.match(stripped))

    def contextual_query(self, text: str) -> str:
        """Build a self-contained retrieval query from a follow-up.

        Deliberately a string join rather than an LLM rewrite: it costs no
        latency, cannot hallucinate a new topic, and works well because
        retrieval is embedding-based and tolerant of extra context.
        """
        if not self.needs_context(text):
            return text
        prior = [t.user for t in self.turns[-2:] if t.user]
        return f"{' '.join(prior)} {text}".strip() if prior else text


class SessionStore:
    """In-memory session store.

    Deliberately not Redis: prototype scope is a single process, and keeping
    conversation context out of any persistent store is the privacy-preferred
    default. PRODUCTION_ROADMAP.md covers the swap for multi-instance serving.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, *, channel: str = "browser") -> Session:
        with self._lock:
            self._evict_expired()
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id=session_id, channel=channel)
                self._sessions[session_id] = session
            session.touch()
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.expired:
                del self._sessions[session_id]
                return None
            return session

    def end(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.pop(session_id, None)

    def _evict_expired(self) -> None:
        for sid in [s for s, sess in self._sessions.items() if sess.expired]:
            del self._sessions[sid]

    @property
    def active_count(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._sessions)


session_store = SessionStore()
