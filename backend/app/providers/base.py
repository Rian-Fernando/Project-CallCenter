"""Provider interfaces — the seam that keeps this prototype vendor-neutral (§27).

Every external capability the application depends on is declared here as an
abstract base class. Application code imports ONLY these types; it never
imports `ollama`, `faster_whisper`, `piper`, or any SDK directly.

The practical consequence: migrating from the free local stack to a hosted
production stack means adding a file under the relevant subpackage and changing
one environment variable. No call site changes.

    LLMProvider          -> OllamaProvider      | Gemini / OpenAI / other hosted
    SpeechToTextProvider -> LocalWhisperProvider| Cloud Whisper / Deepgram
    TextToSpeechProvider -> PiperProvider       | ElevenLabs / Azure / Polly
    EmbeddingProvider    -> OllamaEmbedding     | OpenAI / Voyage / Cohere
    VectorStoreProvider  -> QdrantProvider      | Pinecone / Weaviate / pgvector
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

class HealthState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"      # usable, but a fallback is in play
    UNAVAILABLE = "unavailable"


@dataclass
class HealthStatus:
    """What /api/health reports for each provider.

    `hint` is written for a developer staring at a red dot at 11pm — it should
    say exactly which command fixes the problem.
    """
    name: str
    state: HealthState
    detail: str = ""
    hint: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.state is HealthState.OK


class Provider(ABC):
    """Common lifecycle for every pluggable backend service."""

    name: str = "provider"

    async def startup(self) -> None:
        """Optional eager initialization (model loading, connection setup)."""

    async def shutdown(self) -> None:
        """Release resources."""

    @abstractmethod
    async def health(self) -> HealthStatus:
        """Cheap liveness probe. Must never raise — report UNAVAILABLE instead."""


# --------------------------------------------------------------------------
# Large language models
# --------------------------------------------------------------------------

@dataclass
class ChatMessage:
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Provider):
    """Text generation."""

    name = "llm"

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Generate a complete response.

        `json_mode` asks the provider to constrain output to valid JSON.
        Implementations that cannot enforce this must still make a best effort
        and let the caller handle parse failure.
        """

    @abstractmethod
    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield response text incrementally.

        Streaming matters for the voice demo: it lets speech synthesis start on
        the first sentence instead of waiting for the full answer.
        """


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------

class EmbeddingProvider(Provider):
    name = "embedding"
    dimension: int = 768

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


# --------------------------------------------------------------------------
# Speech to text
# --------------------------------------------------------------------------

@dataclass
class TranscriptionResult:
    text: str
    language: str = "en"
    duration_ms: int = 0
    audio_seconds: float = 0.0
    # Mean model confidence in [0,1]. Used to detect "the mic caught nothing
    # useful" and re-prompt, rather than sending noise into the router.
    confidence: float | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)


class SpeechToTextProvider(Provider):
    name = "stt"

    @abstractmethod
    async def transcribe(
        self, audio: bytes, *, content_type: str = "audio/webm", language: str = "en",
    ) -> TranscriptionResult:
        """Transcribe an audio buffer.

        Implementations must accept whatever the browser's MediaRecorder emits
        (typically webm/opus) as well as wav — callers should not have to
        transcode.
        """


# --------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------

@dataclass
class SynthesisResult:
    audio: bytes
    content_type: str = "audio/wav"
    sample_rate: int = 22050
    duration_ms: int = 0
    voice: str = ""
    # When True, the backend produced no audio and the browser should fall back
    # to the Web Speech API. This keeps the demo working even if every local
    # TTS engine is unavailable.
    client_side_fallback: bool = False


class TextToSpeechProvider(Provider):
    name = "tts"

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str | None = None) -> SynthesisResult:
        ...

    def is_available(self) -> bool:
        """Synchronous, cheap check used by the factory's fallback chain.

        Must not raise and must not do network I/O — it runs during provider
        selection at startup. Return False to let the factory try the next
        engine in the chain.
        """
        return True


# --------------------------------------------------------------------------
# Vector store
# --------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    """One retrieved passage with the provenance needed to cite it (§7)."""
    text: str
    score: float
    doc_id: str = ""
    title: str = ""
    url: str = ""
    department: str = "general"
    source_type: str = ""
    is_official: bool = False
    fetched_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_source(self) -> dict[str, Any]:
        """Citation shape returned by the API and rendered in the UI."""
        return {
            "title": self.title,
            "url": self.url,
            "department": self.department,
            "score": round(self.score, 4),
            "is_official": self.is_official,
            "source_type": self.source_type,
            "fetched_at": self.fetched_at,
            "snippet": self.text[:300].strip(),
        }


class VectorStoreProvider(Provider):
    name = "vector_store"

    @abstractmethod
    async def search(
        self, query: str, *, top_k: int = 6, department: str | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        ...

    @abstractmethod
    async def count(self) -> int:
        """Number of indexed chunks. Zero means the KB was never ingested."""
