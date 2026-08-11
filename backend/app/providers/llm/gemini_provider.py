"""Google Gemini LLM provider — free tier, much faster than local inference.

WHY YOU MIGHT WANT THIS
    Local qwen3:8b on a 16GB Mac: ~2-3s to first token.
    Gemini Flash over the network:  ~0.3-0.6s to first token.
    Quality is also higher, particularly on instruction-following.

    Google's free tier is genuinely free — no card required, generous daily
    request limits, ample for a prototype or a demo.

⚠️  READ THIS BEFORE ENABLING IT FOR A MUNICIPAL SYSTEM  ⚠️

    On Google's UNPAID tier, prompts and responses are used to improve Google's
    products, and may be reviewed by human raters. Every resident question sent
    through this provider becomes training data.

    For a Village evaluating a resident-facing service, that is a governance
    decision — not a technical default. The paid tier excludes training use;
    the free tier does not.

    It also means resident text leaves the machine, which the local Ollama
    default never does.

    Recommended posture:
      * Local Ollama    for anything involving real resident questions
      * Gemini free     for speed demos and development, with test questions
      * Gemini paid     if the Village accepts a hosted model, with a DPA

    See SECURITY_ROADMAP.md § "Data residency and vendor agreements".

SETUP
    1. Get a free key at https://aistudio.google.com/apikey
    2. .env:  LLM_PROVIDER=gemini
              GEMINI_API_KEY=your_key_here
              GEMINI_MODEL=gemini-2.0-flash
    3. Restart the backend. No other change is required.

Implemented with plain httpx against the REST API — no SDK dependency, so the
request shape stays visible and portable.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.core.errors import LLMError, LLMTimeoutError, ServiceUnavailableError
from app.providers.base import (
    ChatMessage, HealthState, HealthStatus, LLMProvider, LLMResponse,
)

log = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = (api_key or settings.gemini_api_key or "").strip()
        self.model = (model or settings.gemini_model).strip()
        self.timeout = settings.ollama_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=API_ROOT,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    def _payload(
        self, messages: list[ChatMessage], temperature: float | None,
        max_tokens: int | None, json_mode: bool,
    ) -> dict:
        # Gemini takes system instructions separately, and uses "model" rather
        # than "assistant" for its own turns.
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages if m.role != "system"
        ]

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": (settings.llm_temperature if temperature is None
                                else temperature),
                "maxOutputTokens": max_tokens or settings.llm_max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        return payload

    @staticmethod
    def _extract(data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()

    def _require_key(self) -> None:
        if not self.api_key:
            raise ServiceUnavailableError(
                "Gemini", "GEMINI_API_KEY is not set",
                hint="Get a free key at https://aistudio.google.com/apikey "
                     "and set GEMINI_API_KEY in .env",
            )

    # ------------------------------------------------------------------
    async def health(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(
                name="llm", state=HealthState.UNAVAILABLE,
                detail="GEMINI_API_KEY is not set.",
                hint="Set GEMINI_API_KEY in .env, or use LLM_PROVIDER=ollama "
                     "to stay fully local.",
                meta={"provider": "gemini", "model": self.model},
            )
        try:
            response = await self.client.get(
                f"/models/{self.model}", params={"key": self.api_key}, timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return HealthStatus(
                name="llm", state=HealthState.UNAVAILABLE,
                detail=f"Gemini returned HTTP {exc.response.status_code}.",
                hint=("Check GEMINI_API_KEY and that GEMINI_MODEL names a model "
                      "your key can access."),
                meta={"provider": "gemini", "model": self.model},
            )
        except Exception as exc:
            return HealthStatus(
                name="llm", state=HealthState.UNAVAILABLE,
                detail=f"Cannot reach the Gemini API: {exc}",
                hint="Check network access, or switch to LLM_PROVIDER=ollama.",
                meta={"provider": "gemini"},
            )

        return HealthStatus(
            name="llm", state=HealthState.OK,
            detail=f"Gemini ready ({self.model})",
            meta={
                "provider": "gemini", "model": self.model,
                "privacy_warning": (
                    "Free-tier prompts and responses may be used by Google to "
                    "improve its products and may be human-reviewed. Resident "
                    "data leaves this machine."
                ),
            },
        )

    # ------------------------------------------------------------------
    async def complete(
        self, messages: list[ChatMessage], *, temperature: float | None = None,
        max_tokens: int | None = None, model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        self._require_key()
        target = model or self.model
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"/models/{target}:generateContent",
                params={"key": self.api_key},
                json=self._payload(messages, temperature, max_tokens, json_mode),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Gemini timed out after {self.timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise LLMError(
                    "Gemini free-tier rate limit reached. Wait a minute, or "
                    "switch to LLM_PROVIDER=ollama."
                ) from exc
            raise LLMError(
                f"Gemini returned {exc.response.status_code}: "
                f"{exc.response.text[:300]}"
            ) from exc
        except Exception as exc:
            raise LLMError(f"Gemini request failed: {exc}") from exc

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=self._extract(data),
            model=target,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            duration_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )

    async def stream(
        self, messages: list[ChatMessage], *, temperature: float | None = None,
        max_tokens: int | None = None, model: str | None = None,
    ) -> AsyncIterator[str]:
        self._require_key()
        target = model or self.model
        try:
            async with self.client.stream(
                "POST",
                f"/models/{target}:streamGenerateContent",
                params={"key": self.api_key, "alt": "sse"},
                json=self._payload(messages, temperature, max_tokens, False),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    body = line[6:].strip()
                    if not body or body == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if piece := self._extract(chunk):
                        yield piece
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Gemini stream timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Gemini stream returned {exc.response.status_code}") from exc
