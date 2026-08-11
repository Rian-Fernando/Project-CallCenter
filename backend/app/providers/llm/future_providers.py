"""Production LLM providers — deliberately NOT implemented (§3, §27).

The free prototype must cost $0, so none of these are wired up. They exist as
executable documentation: each one states precisely what implementing it
requires, so the migration is a filling-in exercise rather than a redesign.

NOTE: Gemini is fully implemented in gemini_provider.py and is free to use.

TO ENABLE ANY OF THESE
  1. pip install the SDK named in the class docstring
  2. add the API key to `.env`
  3. implement `complete()` and `stream()` below
  4. register the class in `app/providers/factory.py::_LLM_PROVIDERS`
  5. set `LLM_PROVIDER=<name>` in `.env`

No other file in the application changes. Application code depends only on the
`LLMProvider` interface.

COST WARNING: every provider here bills per token. Read PRODUCTION_ROADMAP.md
for volume estimates before pointing a real phone line at one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.providers.base import (
    ChatMessage, HealthState, HealthStatus, LLMProvider, LLMResponse,
)


class _UnimplementedProvider(LLMProvider):
    """Shared behavior: fail loudly and instructively, never silently."""

    vendor = "unknown"
    package = ""
    env_key = ""
    docs = ""
    notes = ""

    def _explain(self) -> str:
        return (
            f"{self.vendor} provider is not implemented in this prototype.\n"
            f"  1. pip install {self.package}\n"
            f"  2. set {self.env_key} in .env\n"
            f"  3. implement complete()/stream() in "
            f"backend/app/providers/llm/future_providers.py\n"
            f"  4. register it in app/providers/factory.py\n"
            f"  docs: {self.docs}"
        )

    async def complete(self, messages: list[ChatMessage], **kwargs) -> LLMResponse:
        raise NotImplementedError(self._explain())

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError(self._explain())
        yield ""  # pragma: no cover — makes this an async generator

    async def health(self) -> HealthStatus:
        return HealthStatus(
            name="llm", state=HealthState.UNAVAILABLE,
            detail=f"{self.vendor} provider is a documented stub.",
            hint="Set LLM_PROVIDER=ollama to use the free local model.",
            meta={"vendor": self.vendor, "package": self.package, "notes": self.notes},
        )



class OpenAIProvider(_UnimplementedProvider):
    """GPT models via the OpenAI API.

    Mapping notes:
      - `messages` maps 1:1; role="system" is supported directly.
      - For `json_mode`, pass response_format={"type": "json_object"} — this is
        a stronger guarantee than Ollama's `format: json`.
      - Streaming yields chunks at `.choices[0].delta.content`.
    """
    vendor = "OpenAI"
    package = "openai"
    env_key = "OPENAI_API_KEY"
    docs = "https://platform.openai.com/docs/api-reference/chat"
    notes = "Native JSON schema mode is a good fit for the intent router."

