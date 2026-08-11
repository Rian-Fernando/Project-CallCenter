"""Piper text-to-speech — local, free, natural-sounding.

Piper runs an ONNX voice model on the CPU. It is fast enough for real-time
conversation on Apple Silicon and needs no network access after the voice file
is downloaded once.

Voice files come from the Rhasspy HuggingFace repository and are ~65MB. The
download happens automatically on first use.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.errors import SynthesisError
from app.providers.base import (
    HealthState, HealthStatus, SynthesisResult, TextToSpeechProvider,
)

log = logging.getLogger(__name__)

VOICE_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Maps a voice name to its path within the HuggingFace voice repository.
KNOWN_VOICES: dict[str, str] = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-lessac-high": "en/en_US/lessac/high/en_US-lessac-high.onnx",
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
    "en_GB-alba-medium": "en/en_GB/alba/medium/en_GB-alba-medium.onnx",
}


class PiperProvider(TextToSpeechProvider):
    def __init__(self, voice: str | None = None):
        self.voice = voice or settings.piper_voice
        self.voice_dir = settings.piper_voice_path
        self._voice_obj = None
        self._lock = asyncio.Lock()

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        try:
            import piper  # noqa: F401
        except Exception:
            return False
        return self.voice in KNOWN_VOICES or self._model_path.exists()

    @property
    def _model_path(self) -> Path:
        return self.voice_dir / f"{self.voice}.onnx"

    @property
    def _config_path(self) -> Path:
        return self.voice_dir / f"{self.voice}.onnx.json"

    # -- model download ----------------------------------------------------
    async def _download_voice(self) -> None:
        if self.voice not in KNOWN_VOICES:
            raise SynthesisError(
                f"Unknown Piper voice '{self.voice}'. "
                f"Known: {', '.join(sorted(KNOWN_VOICES))}"
            )
        rel = KNOWN_VOICES[self.voice]
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        log.info("Downloading Piper voice '%s' (~65MB, one time)...", self.voice)

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            for url, dest in (
                (f"{VOICE_BASE_URL}/{rel}", self._model_path),
                (f"{VOICE_BASE_URL}/{rel}.json", self._config_path),
            ):
                tmp = dest.with_suffix(dest.suffix + ".part")
                try:
                    async with client.stream("GET", url) as r:
                        r.raise_for_status()
                        with tmp.open("wb") as fh:
                            async for block in r.aiter_bytes(1 << 16):
                                fh.write(block)
                    # Atomic rename: a killed download never leaves a corrupt
                    # file that looks complete on the next run.
                    tmp.replace(dest)
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    raise SynthesisError(f"Piper voice download failed: {exc}") from exc
        log.info("Piper voice ready at %s", self._model_path)

    async def _ensure_loaded(self):
        if self._voice_obj is not None:
            return self._voice_obj
        async with self._lock:
            if self._voice_obj is not None:
                return self._voice_obj
            if not (self._model_path.exists() and self._config_path.exists()):
                await self._download_voice()
            try:
                from piper import PiperVoice
                self._voice_obj = await asyncio.to_thread(
                    PiperVoice.load, str(self._model_path)
                )
            except Exception as exc:
                raise SynthesisError(f"Could not load Piper voice: {exc}") from exc
        return self._voice_obj

    async def startup(self) -> None:
        """Warm the model so the first caller doesn't pay the load cost."""
        try:
            await self._ensure_loaded()
        except Exception as exc:
            log.warning("Piper warm-up skipped: %s", exc)

    # -- health ------------------------------------------------------------
    async def health(self) -> HealthStatus:
        try:
            import piper  # noqa: F401
        except Exception as exc:
            return HealthStatus(
                name="tts", state=HealthState.UNAVAILABLE,
                detail=f"piper-tts not importable: {exc}",
                hint="Run:  backend/.venv/bin/pip install piper-tts",
                meta={"engine": "piper"},
            )
        if not self._model_path.exists():
            return HealthStatus(
                name="tts", state=HealthState.DEGRADED,
                detail=f"Voice '{self.voice}' not downloaded yet (downloads on first use).",
                hint="It will download automatically, or run: ./scripts/fetch_voice.sh",
                meta={"engine": "piper", "voice": self.voice},
            )
        return HealthStatus(
            name="tts", state=HealthState.OK,
            detail=f"Piper ready ({self.voice})",
            meta={"engine": "piper", "voice": self.voice},
        )

    # -- synthesis ---------------------------------------------------------
    async def synthesize(self, text: str, *, voice: str | None = None) -> SynthesisResult:
        text = (text or "").strip()
        if not text:
            raise SynthesisError("Cannot synthesize empty text.")

        voice_obj = await self._ensure_loaded()
        started = time.perf_counter()

        def _run() -> tuple[bytes, int]:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                voice_obj.synthesize_wav(text, wav)
            with wave.open(io.BytesIO(buf.getvalue()), "rb") as r:
                rate = r.getframerate()
            return buf.getvalue(), rate

        try:
            audio, sample_rate = await asyncio.to_thread(_run)
        except Exception as exc:
            raise SynthesisError(f"Piper synthesis failed: {exc}") from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        return SynthesisResult(
            audio=audio, content_type="audio/wav", sample_rate=sample_rate,
            duration_ms=elapsed, voice=self.voice,
        )
