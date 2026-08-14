"""Local speech-to-text via faster-whisper.

faster-whisper reimplements Whisper on CTranslate2. Chosen over openai-whisper
because it needs no PyTorch (saving ~2.5GB of install and a large chunk of RAM)
and runs several times faster on CPU with int8 quantization.

The model downloads automatically on first use (~145MB for base.en).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from app.core.config import settings
from app.core.errors import TranscriptionError
from app.providers.base import (
    HealthState, HealthStatus, SpeechToTextProvider, TranscriptionResult,
)

log = logging.getLogger(__name__)

# Extension used for the temp file, per browser MIME type. faster-whisper reads
# via PyAV (bundled ffmpeg), which sniffs the container, but a correct
# extension avoids ambiguity for edge-case formats.
_EXT_BY_TYPE = {
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4", "audio/m4a": ".m4a", "audio/flac": ".flac",
}

# Biases the decoder toward vocabulary a Garden City caller actually uses.
# Whisper weighs this as prior context, so proper nouns and municipal terms it
# would otherwise render as similar-sounding common words become far more
# likely. Written as natural prose because that is how the model was trained to
# read a prompt — a bare word list works markedly less well.
DOMAIN_PROMPT = (
    "This is a call to the Village of Garden City, New York. Residents ask "
    "about garbage, trash, rubbish and recycling collection, bulk pickup, "
    "sanitation, potholes, street lights, snow plowing, water and sewer, "
    "building permits, inspections, certificates of occupancy, zoning and "
    "variances, parking permits, railroad and commuter parking, parking "
    "tickets, property taxes, water bills, the Village Clerk, birth and "
    "marriage certificates, recreation programs, the pool, tennis courts, "
    "St. Paul's, the Senior Center, and the Recycling Center on Cherry Valley "
    "Avenue. Streets include Rockaway Avenue, Stewart Avenue, Franklin Avenue, "
    "Nassau Boulevard, Clinton Road, Cherry Valley Avenue and Hempstead "
    "Turnpike. They may mention Nassau County, the Town of Hempstead, "
    "Village Hall, Public Works, or the Building Department. Typical questions "
    "are: Who handles gardening? Who do I call about a pothole? What day is my "
    "trash picked up? How do I get a permit? What is the phone number for the "
    "Building Department? When does the pool open? Who handles leaf pickup, "
    "yard waste, landscaping, tree trimming and snow removal?"
)


class LocalWhisperProvider(SpeechToTextProvider):
    def __init__(self, model_size: str | None = None):
        self.model_size = model_size or settings.whisper_model
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self):
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                raise TranscriptionError(f"faster-whisper is not installed: {exc}") from exc

            log.info("Loading Whisper model '%s' (downloads on first run)...",
                     self.model_size)
            try:
                self._model = await asyncio.to_thread(
                    WhisperModel, self.model_size,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    download_root=str(settings.resolve("./data/models/whisper")),
                )
            except Exception as exc:
                raise TranscriptionError(f"Could not load Whisper model: {exc}") from exc
            log.info("Whisper model ready")
        return self._model

    async def startup(self) -> None:
        try:
            await self._ensure_loaded()
        except Exception as exc:
            log.warning("Whisper warm-up skipped: %s", exc)

    async def health(self) -> HealthStatus:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:
            return HealthStatus(
                name="stt", state=HealthState.UNAVAILABLE,
                detail=f"faster-whisper not importable: {exc}",
                hint="Run:  backend/.venv/bin/pip install faster-whisper",
            )
        loaded = self._model is not None
        return HealthStatus(
            name="stt",
            state=HealthState.OK if loaded else HealthState.DEGRADED,
            detail=(f"Whisper '{self.model_size}' loaded" if loaded
                    else f"Whisper '{self.model_size}' will load on first use"),
            hint="" if loaded else "The first transcription takes a few extra seconds.",
            meta={"model": self.model_size, "loaded": loaded,
                  "compute_type": settings.whisper_compute_type},
        )

    async def transcribe(
        self, audio: bytes, *, content_type: str = "audio/webm", language: str = "en",
    ) -> TranscriptionResult:
        if not audio:
            raise TranscriptionError("Received empty audio.")

        model = await self._ensure_loaded()
        started = time.perf_counter()
        suffix = _EXT_BY_TYPE.get(content_type.split(";")[0].strip().lower(), ".webm")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio)
            tmp.flush()

            def _run():
                segments, info = model.transcribe(
                    tmp.name,
                    language=language or None,
                    beam_size=settings.whisper_beam_size,
                    # Whisper hallucinates confident text on silence; the VAD
                    # filter drops non-speech before decoding, which is the
                    # single most effective fix for phantom transcripts.
                    vad_filter=True,
                    # 700ms rather than 500: callers pause mid-sentence, and
                    # clipping those pauses truncated real words.
                    vad_parameters={
                        "min_silence_duration_ms": 700,
                        # Keep a little audio either side of detected speech so
                        # word onsets and endings survive the trim.
                        "speech_pad_ms": 200,
                    },
                    # Priming the decoder with the vocabulary it is likely to
                    # hear is the single cheapest accuracy win available. Whisper
                    # has no idea "Rockaway" or "Nassau" are plausible words in
                    # this context and will happily produce a common word that
                    # sounds similar.
                    initial_prompt=DOMAIN_PROMPT,
                    # Each turn is independent; carrying context across them
                    # lets one bad transcript poison the next.
                    condition_on_previous_text=False,
                    # Retry with progressively higher temperature when the
                    # decode looks degenerate, instead of emitting nonsense.
                    temperature=[0.0, 0.2, 0.4],
                    compression_ratio_threshold=2.4,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.6,
                )
                return list(segments), info

            try:
                segments, info = await asyncio.to_thread(_run)
            except Exception as exc:
                raise TranscriptionError(f"Transcription failed: {exc}") from exc

        text = " ".join(s.text.strip() for s in segments).strip()

        # avg_logprob is a log probability (<= 0). exp() maps it to a rough
        # 0..1 confidence, good enough to spot "the mic picked up nothing".
        import math
        confidence = None
        if segments:
            avg = sum(getattr(s, "avg_logprob", 0.0) for s in segments) / len(segments)
            confidence = round(min(1.0, math.exp(avg)), 4)

        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", language) or language,
            duration_ms=int((time.perf_counter() - started) * 1000),
            audio_seconds=round(getattr(info, "duration", 0.0) or 0.0, 2),
            confidence=confidence,
            segments=[
                {"start": round(s.start, 2), "end": round(s.end, 2),
                 "text": s.text.strip()}
                for s in segments
            ],
        )
