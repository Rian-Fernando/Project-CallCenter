"""Browser Web Speech API — the last-resort TTS fallback.

Produces no server-side audio. Instead it returns `client_side_fallback=True`,
which tells the frontend to speak the text with `window.speechSynthesis`.

This provider cannot fail, which is the point: it guarantees the voice demo
always has *some* spoken output even if every local engine is broken.
"""

from __future__ import annotations

from app.providers.base import (
    HealthState, HealthStatus, SynthesisResult, TextToSpeechProvider,
)


class BrowserTTSProvider(TextToSpeechProvider):
    def is_available(self) -> bool:
        return True

    async def health(self) -> HealthStatus:
        return HealthStatus(
            name="tts", state=HealthState.DEGRADED,
            detail="Using the browser's built-in speech synthesis (no local TTS engine).",
            hint="For better voice quality install Piper: "
                 "backend/.venv/bin/pip install piper-tts",
            meta={"engine": "browser", "client_side": True},
        )

    async def synthesize(self, text: str, *, voice: str | None = None) -> SynthesisResult:
        return SynthesisResult(
            audio=b"", content_type="application/json", duration_ms=0,
            voice="browser", client_side_fallback=True,
        )
