"""macOS `say` text-to-speech — first fallback when Piper is unavailable.

Not part of the original plan, added for demo robustness: macOS ships 180+
system voices and `say` is always present on a Mac. If a Piper voice download
fails on presentation day, the demo still speaks.

Free, fully local, no install. macOS only.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from app.core.errors import SynthesisError
from app.providers.base import (
    HealthState, HealthStatus, SynthesisResult, TextToSpeechProvider,
)

log = logging.getLogger(__name__)

DEFAULT_VOICE = "Samantha"
SAMPLE_RATE = 22050


class MacOSSayProvider(TextToSpeechProvider):
    def __init__(self, voice: str | None = None):
        self.voice = voice or DEFAULT_VOICE

    def is_available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("say") is not None

    async def health(self) -> HealthStatus:
        if not self.is_available():
            return HealthStatus(
                name="tts", state=HealthState.UNAVAILABLE,
                detail="`say` is only available on macOS.",
                hint="Use TTS_PROVIDER=piper instead.",
                meta={"engine": "macos_say"},
            )
        return HealthStatus(
            name="tts", state=HealthState.OK,
            detail=f"macOS say ready ({self.voice}) — fallback engine",
            meta={"engine": "macos_say", "voice": self.voice},
        )

    async def synthesize(self, text: str, *, voice: str | None = None) -> SynthesisResult:
        text = (text or "").strip()
        if not text:
            raise SynthesisError("Cannot synthesize empty text.")
        if not self.is_available():
            raise SynthesisError("macOS `say` is not available on this platform.")

        started = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "speech.wav"
            # --data-format=LEI16@22050 emits 16-bit little-endian PCM WAV,
            # which every browser can play without transcoding.
            proc = await asyncio.create_subprocess_exec(
                "say", "-v", voice or self.voice,
                "--data-format=LEI16@22050", "-o", str(out), text,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not out.exists():
                raise SynthesisError(
                    f"`say` failed ({proc.returncode}): {stderr.decode(errors='replace')[:200]}"
                )
            audio = out.read_bytes()

        return SynthesisResult(
            audio=audio, content_type="audio/wav", sample_rate=SAMPLE_RATE,
            duration_ms=int((time.perf_counter() - started) * 1000),
            voice=voice or self.voice,
        )
