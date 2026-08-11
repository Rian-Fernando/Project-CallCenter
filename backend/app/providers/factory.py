"""Provider registry — the single place vendors are chosen.

Every provider is selected here by name from `.env`. This is the only module in
the application that knows which concrete implementation is in use; everything
else depends on the abstract interfaces in `providers/base.py`.

Adding a vendor = write the class + add one line to the relevant dict below.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.providers.base import (
    EmbeddingProvider, HealthStatus, LLMProvider, SpeechToTextProvider,
    TextToSpeechProvider, VectorStoreProvider,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Registries.  Values are import paths, resolved lazily so that an unused
# provider's dependencies are never imported (and never need installing).
# --------------------------------------------------------------------------

_LLM_PROVIDERS: dict[str, str] = {
    "ollama": "app.providers.llm.ollama_provider:OllamaProvider",
    # --- hosted alternatives ---
    "openai": "app.providers.llm.future_providers:OpenAIProvider",
    "gemini": "app.providers.llm.gemini_provider:GeminiProvider",
}

_EMBEDDING_PROVIDERS: dict[str, str] = {
    "ollama": "app.providers.embeddings.ollama_embeddings:OllamaEmbeddingProvider",
}

_STT_PROVIDERS: dict[str, str] = {
    "local_whisper": "app.providers.stt.whisper_provider:LocalWhisperProvider",
}

_TTS_PROVIDERS: dict[str, str] = {
    "kokoro": "app.providers.tts.kokoro_provider:KokoroProvider",
    "piper": "app.providers.tts.piper_provider:PiperProvider",
    "macos_say": "app.providers.tts.macos_say_provider:MacOSSayProvider",
    "browser": "app.providers.tts.browser_provider:BrowserTTSProvider",
}


def _resolve(path: str) -> type:
    module_path, _, class_name = path.partition(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def _pick(registry: dict[str, str], name: str, kind: str) -> type:
    key = name.strip().lower()
    if key not in registry:
        raise ValueError(
            f"Unknown {kind} provider '{name}'. "
            f"Available: {', '.join(sorted(registry))}"
        )
    return _resolve(registry[key])


class ProviderRegistry:
    """Lazily constructs and caches one instance of each provider."""

    def __init__(self) -> None:
        self._llm: LLMProvider | None = None
        self._embedding: EmbeddingProvider | None = None
        self._vector_store: VectorStoreProvider | None = None
        self._stt: SpeechToTextProvider | None = None
        self._tts: TextToSpeechProvider | None = None

    # -- accessors ---------------------------------------------------------
    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = _pick(_LLM_PROVIDERS, settings.llm_provider, "LLM")()
            log.info("LLM provider: %s", settings.llm_provider)
        return self._llm

    @property
    def embedding(self) -> EmbeddingProvider:
        if self._embedding is None:
            self._embedding = _pick(
                _EMBEDDING_PROVIDERS, settings.embedding_provider, "embedding")()
        return self._embedding

    @property
    def vector_store(self) -> VectorStoreProvider:
        if self._vector_store is None:
            from app.providers.vectorstore.qdrant_provider import QdrantProvider
            self._vector_store = QdrantProvider(self.embedding)
        return self._vector_store

    @property
    def stt(self) -> SpeechToTextProvider:
        if self._stt is None:
            self._stt = _pick(_STT_PROVIDERS, settings.stt_provider, "STT")()
        return self._stt

    @property
    def tts(self) -> TextToSpeechProvider:
        """Text-to-speech with automatic degradation.

        The configured engine is tried first. If it cannot initialize, we fall
        back through progressively simpler options so a demo is never blocked
        by a TTS install problem:

            piper  ->  macOS `say`  ->  browser Web Speech API

        The browser fallback always works: it returns no audio and signals the
        frontend to speak the text itself.
        """
        if self._tts is None:
            chain = [settings.tts_provider]
            if settings.tts_fallback_enabled:
                for candidate in ("kokoro", "piper", "macos_say", "browser"):
                    if candidate not in chain:
                        chain.append(candidate)

            for name in chain:
                try:
                    provider = _pick(_TTS_PROVIDERS, name, "TTS")()
                    if provider.is_available():
                        if name != settings.tts_provider:
                            log.warning(
                                "TTS '%s' unavailable; falling back to '%s'.",
                                settings.tts_provider, name,
                            )
                        self._tts = provider
                        break
                    log.info("TTS provider '%s' reports unavailable, trying next.", name)
                except Exception as exc:
                    log.warning("TTS provider '%s' failed to load: %s", name, exc)

            if self._tts is None:  # pragma: no cover — browser provider cannot fail
                from app.providers.tts.browser_provider import BrowserTTSProvider
                self._tts = BrowserTTSProvider()
        return self._tts

    # -- lifecycle ---------------------------------------------------------
    async def health_all(self) -> dict[str, HealthStatus]:
        """Probe every provider concurrently. Never raises."""
        async def probe(label: str, get: Any) -> tuple[str, HealthStatus]:
            try:
                return label, await get().health()
            except Exception as exc:
                from app.providers.base import HealthState
                return label, HealthStatus(
                    name=label, state=HealthState.UNAVAILABLE,
                    detail=f"Provider failed to initialize: {exc}",
                )

        results = await asyncio.gather(*[
            probe("llm", lambda: self.llm),
            probe("embedding", lambda: self.embedding),
            probe("vector_store", lambda: self.vector_store),
            probe("stt", lambda: self.stt),
            probe("tts", lambda: self.tts),
        ])
        return dict(results)

    async def shutdown(self) -> None:
        for provider in (self._llm, self._embedding, self._vector_store,
                         self._stt, self._tts):
            if provider is None:
                continue
            try:
                await provider.shutdown()
            except Exception:
                log.debug("Shutdown failed for %s", provider, exc_info=True)


registry = ProviderRegistry()
