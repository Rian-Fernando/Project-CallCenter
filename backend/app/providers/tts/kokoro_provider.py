"""Kokoro text-to-speech — the natural-sounding local voice.

Kokoro is an 82M-parameter Apache-2.0 model that sounds markedly more human
than Piper: better prosody, natural sentence rhythm, and far less of the flat
"screen reader" cadence. It runs on the CPU via onnxruntime — no GPU, no API
key, no network access after the one-time model download.

TRADE-OFF vs PIPER (measured on an Apple M2 Pro):
    Piper   ~0.2s for a sentence, noticeably robotic
    Kokoro  ~0.4-1.2s for a sentence, clearly more human

Kokoro is slower per call but still 0.23-0.35x realtime — it generates speech
faster than the speech takes to play. Combined with sentence-level streaming
(synthesize sentence 1 while the model writes sentence 2), the caller hears
audio sooner than they did with Piper generating the whole answer at once.

Model files (~337MB total) download automatically on first use.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from pathlib import Path

import httpx
import numpy as np

from app.core.config import settings
from app.core.errors import SynthesisError
from app.providers.base import (
    HealthState, HealthStatus, SynthesisResult, TextToSpeechProvider,
)

log = logging.getLogger(__name__)

RELEASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"

# A curated subset. Kokoro ships ~54 voices; these are the ones that sound
# right for a municipal receptionist — warm, clear, unhurried.
RECOMMENDED_VOICES = {
    "af_heart": "Warm American female — best default for a receptionist",
    "af_bella": "Bright American female",
    "af_nicole": "Softer American female",
    "am_michael": "Calm American male",
    "am_adam": "Neutral American male",
    "bf_emma": "British female",
}
DEFAULT_VOICE = "af_heart"


class KokoroProvider(TextToSpeechProvider):
    def __init__(self, voice: str | None = None):
        configured = (voice or settings.kokoro_voice or DEFAULT_VOICE).strip()
        self.voice = configured
        self.model_dir = settings.resolve(settings.kokoro_model_dir)
        self.speed = settings.kokoro_speed
        self._engine = None
        self._lock = asyncio.Lock()

    # -- paths -------------------------------------------------------------
    @property
    def _model_path(self) -> Path:
        return self.model_dir / MODEL_FILE

    @property
    def _voices_path(self) -> Path:
        return self.model_dir / VOICES_FILE

    def is_available(self) -> bool:
        try:
            import kokoro_onnx  # noqa: F401
        except Exception:
            return False
        return True

    # -- model download ----------------------------------------------------
    async def _download(self) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        targets = [
            (f"{RELEASE_URL}/{MODEL_FILE}", self._model_path, "voice model (~310MB)"),
            (f"{RELEASE_URL}/{VOICES_FILE}", self._voices_path, "voice pack (~27MB)"),
        ]
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            for url, dest, label in targets:
                if dest.exists():
                    continue
                log.info("Downloading Kokoro %s, one time only...", label)
                tmp = dest.with_suffix(dest.suffix + ".part")
                try:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with tmp.open("wb") as fh:
                            async for block in response.aiter_bytes(1 << 18):
                                fh.write(block)
                    # Atomic rename so an interrupted download never leaves a
                    # truncated file that looks complete next run.
                    tmp.replace(dest)
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    raise SynthesisError(f"Kokoro download failed: {exc}") from exc

    async def _ensure_loaded(self):
        if self._engine is not None:
            return self._engine
        async with self._lock:
            if self._engine is not None:
                return self._engine
            if not (self._model_path.exists() and self._voices_path.exists()):
                await self._download()
            try:
                from kokoro_onnx import Kokoro
                self._engine = await asyncio.to_thread(
                    Kokoro, str(self._model_path), str(self._voices_path),
                )
                log.info("Kokoro ready (voice: %s)", self.voice)
            except Exception as exc:
                raise SynthesisError(f"Could not load Kokoro: {exc}") from exc
        return self._engine

    async def startup(self) -> None:
        """Warm the model so the first caller doesn't pay the load cost."""
        try:
            await self._ensure_loaded()
        except Exception as exc:
            log.warning("Kokoro warm-up skipped: %s", exc)

    # -- health ------------------------------------------------------------
    async def health(self) -> HealthStatus:
        try:
            import kokoro_onnx  # noqa: F401
        except Exception as exc:
            return HealthStatus(
                name="tts", state=HealthState.UNAVAILABLE,
                detail=f"kokoro-onnx not importable: {exc}",
                hint="Run:  backend/.venv/bin/pip install kokoro-onnx",
                meta={"engine": "kokoro"},
            )
        if not self._model_path.exists():
            return HealthStatus(
                name="tts", state=HealthState.DEGRADED,
                detail="Kokoro model not downloaded yet (~337MB on first use).",
                hint="It downloads automatically on the first spoken response.",
                meta={"engine": "kokoro", "voice": self.voice},
            )
        return HealthStatus(
            name="tts",
            state=HealthState.OK if self._engine else HealthState.DEGRADED,
            detail=(f"Kokoro ready ({self.voice})" if self._engine
                    else f"Kokoro '{self.voice}' loads on first use"),
            meta={"engine": "kokoro", "voice": self.voice,
                  "available_voices": list(RECOMMENDED_VOICES)},
        )

    # -- synthesis ---------------------------------------------------------
    async def synthesize(self, text: str, *, voice: str | None = None) -> SynthesisResult:
        text = (text or "").strip()
        if not text:
            raise SynthesisError("Cannot synthesize empty text.")

        engine = await self._ensure_loaded()
        chosen = (voice or self.voice).strip()
        started = time.perf_counter()

        def _run() -> tuple[bytes, int]:
            samples, sample_rate = engine.create(
                text, voice=chosen, speed=self.speed, lang="en-us",
            )
            # Kokoro returns float32 in [-1, 1]; browsers need 16-bit PCM WAV.
            pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
            pcm = (pcm * 32767).astype(np.int16)

            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm.tobytes())
            return buffer.getvalue(), sample_rate

        try:
            audio, sample_rate = await asyncio.to_thread(_run)
        except Exception as exc:
            raise SynthesisError(f"Kokoro synthesis failed: {exc}") from exc

        return SynthesisResult(
            audio=audio, content_type="audio/wav", sample_rate=sample_rate,
            duration_ms=int((time.perf_counter() - started) * 1000),
            voice=chosen,
        )
