"""Ollama LLM provider — the free, local default.

Talks to the Ollama HTTP API directly with httpx. No SDK dependency, which
keeps the surface small and makes the request shape obvious to anyone reading
the code who needs to port it to another vendor.
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


class OllamaProvider(LLMProvider):
    """Local inference via Ollama (https://ollama.com)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        *,
        thinking: bool | None = None,
        timeout: float | None = None,
    ):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.thinking = settings.ollama_thinking if thinking is None else thinking
        self.timeout = timeout or settings.ollama_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
            )
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- health ------------------------------------------------------------
    async def health(self) -> HealthStatus:
        hint = "Run:  brew services start ollama"
        try:
            r = await self.client.get("/api/tags", timeout=5.0)
            r.raise_for_status()
            installed = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as exc:
            return HealthStatus(
                name="llm", state=HealthState.UNAVAILABLE,
                detail=f"Cannot reach Ollama at {self.base_url}: {exc}",
                hint=hint,
                meta={"provider": "ollama", "base_url": self.base_url},
            )

        # Ollama reports "qwen3:8b"; a user may configure bare "qwen3".
        if not any(m == self.model or m.split(":")[0] == self.model.split(":")[0]
                   for m in installed):
            return HealthStatus(
                name="llm", state=HealthState.DEGRADED,
                detail=f"Model '{self.model}' is not installed.",
                hint=f"Run:  ollama pull {self.model}",
                meta={"provider": "ollama", "model": self.model, "installed": installed},
            )

        return HealthStatus(
            name="llm", state=HealthState.OK,
            detail=f"Ollama ready with {self.model}",
            meta={
                "provider": "ollama", "model": self.model,
                "thinking": self.thinking, "installed": installed,
            },
        )

    # -- helpers -----------------------------------------------------------
    def _payload(
        self, messages: list[ChatMessage], temperature: float | None,
        max_tokens: int | None, model: str | None, *, stream: bool,
        json_mode: bool = False,
    ) -> dict:
        payload: dict = {
            "model": model or self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            # Benchmarked on an M2 Pro: leaving Qwen3's reasoning phase enabled
            # cost 9.93s vs 1.29s for an identical routing result. Voice needs
            # the fast path.
            "think": self.thinking,
            # Keep the model resident between turns. Ollama's default unloads
            # after 5 minutes, and a cold reload costs ~5s — which lands
            # squarely on the next caller during a demo.
            "keep_alive": settings.ollama_keep_alive,
            "options": {
                "temperature": settings.llm_temperature if temperature is None else temperature,
                "num_predict": max_tokens or settings.llm_max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
        return payload

    @staticmethod
    def _raise_for_connection(exc: Exception) -> None:
        raise ServiceUnavailableError(
            "Ollama", str(exc), hint="Run:  brew services start ollama",
        ) from exc

    # -- generation --------------------------------------------------------
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload = self._payload(
            messages, temperature, max_tokens, model, stream=False, json_mode=json_mode,
        )
        started = time.perf_counter()
        try:
            r = await self.client.post("/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Ollama timed out after {self.timeout}s") from exc
        except httpx.ConnectError as exc:
            self._raise_for_connection(exc)
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama returned {exc.response.status_code}: "
                           f"{exc.response.text[:400]}") from exc
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        return LLMResponse(
            text=(data.get("message") or {}).get("content", "").strip(),
            model=data.get("model", payload["model"]),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            duration_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, temperature, max_tokens, model, stream=True)
        try:
            async with self.client.stream("POST", "/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        log.debug("Skipping unparseable stream line: %.120s", line)
                        continue
                    if chunk.get("done"):
                        break
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Ollama stream timed out after {self.timeout}s") from exc
        except httpx.ConnectError as exc:
            self._raise_for_connection(exc)
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama stream returned {exc.response.status_code}") from exc
