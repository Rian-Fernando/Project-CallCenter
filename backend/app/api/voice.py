"""Voice endpoints — transcription, synthesis, and the streaming turn (§5, §6, §18).

CHANNEL INDEPENDENCE (§19): none of these endpoints care whether the audio came
from a browser, a phone bridge, SIP, or WebRTC. They accept bytes and return
bytes. `channel` is recorded for analytics only and never changes AI behavior,
which is what makes a future Twilio/Asterisk front end a drop-in.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.core.errors import TranscriptionError
from app.models.schemas import (
    ChatRequest, SynthesisRequest, TranscriptionResponse,
)
from app.providers.factory import registry
from app.providers.tts.normalization import normalize_for_speech
from app.services.conversation import conversation_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

# Reject oversized uploads before they reach the model. A normal utterance is
# a few hundred KB; anything past this is a mistake or abuse.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.post("/voice/transcribe", response_model=TranscriptionResponse,
             summary="Transcribe recorded audio to text")
async def transcribe(
    audio: UploadFile = File(..., description="Audio blob (webm/opus, wav, mp3, m4a)."),
    language: str = Form("en"),
) -> TranscriptionResponse:
    data = await audio.read()
    if not data:
        raise TranscriptionError("The uploaded audio was empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large.")

    result = await registry.stt.transcribe(
        data, content_type=audio.content_type or "audio/webm", language=language,
    )
    return TranscriptionResponse(
        text=result.text, language=result.language,
        duration_ms=result.duration_ms, audio_seconds=result.audio_seconds,
        confidence=result.confidence,
    )


@router.post("/voice/synthesize", summary="Synthesize speech from text")
async def synthesize(request: SynthesisRequest):
    """Returns `audio/wav` bytes.

    If no local TTS engine is available, returns JSON with
    `client_side_fallback: true` instead, and the browser should speak the text
    with the Web Speech API. Callers must check the content type.
    """
    # Normalize on the way into the engine only. The transcript on screen
    # keeps the original text, because "6:00 a.m." is what a resident should
    # read even though "six A M" is what they should hear.
    spoken = normalize_for_speech(request.text)
    result = await registry.tts.synthesize(spoken, voice=request.voice)

    if result.client_side_fallback:
        return Response(
            content=json.dumps({
                "client_side_fallback": True,
                "text": request.text,
                "reason": "No server-side TTS engine is available.",
            }),
            media_type="application/json",
        )

    return Response(
        content=result.audio,
        media_type=result.content_type,
        headers={
            "X-Voice": result.voice,
            "X-Duration-Ms": str(result.duration_ms),
            "Cache-Control": "no-store",
        },
    )


@router.post("/voice/turn", summary="Audio in, full conversational turn out")
async def voice_turn(
    audio: UploadFile = File(...),
    session_id: str | None = Form(None),
    channel: str = Form("browser"),
) -> dict:
    """One round trip for the voice demo: transcribe, then run the full
    pipeline. Returns the transcript plus the complete turn result.

    Audio for the reply is fetched separately from `/voice/synthesize`, so the
    caller can start rendering the transcript while speech is still generating.
    """
    data = await audio.read()
    if not data:
        raise TranscriptionError("The uploaded audio was empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large.")

    started = time.perf_counter()
    transcription = await registry.stt.transcribe(
        data, content_type=audio.content_type or "audio/webm",
    )

    text = transcription.text.strip()
    if not text:
        # Whisper's VAD filter removes silence, so empty output means the
        # microphone genuinely captured no speech. Re-prompt rather than
        # sending noise into the router.
        return {
            "transcript": "",
            "no_speech_detected": True,
            "answer": "I didn't catch that. Could you say it again?",
            "action": "clarify",
            "session_id": session_id or uuid.uuid4().hex,
            "timings": {"stt_ms": transcription.duration_ms},
        }

    result = await conversation_service.handle(
        session_id or uuid.uuid4().hex, text,
        channel=channel, stt_ms=transcription.duration_ms,
    )
    payload = result.as_dict()
    payload["transcript"] = text
    payload["no_speech_detected"] = False
    payload["timings"]["stt_ms"] = transcription.duration_ms
    payload["timings"]["voice_total_ms"] = int((time.perf_counter() - started) * 1000)
    return payload


@router.post("/chat/stream", summary="Streaming conversational turn (SSE)")
async def chat_stream(request: ChatRequest):
    """Server-Sent Events stream of a single turn.

    Event sequence:
        meta      routing + confidence + sources, as soon as they are known
        delta     incremental answer text
        done      final persisted turn record
        error     something failed; the message is safe to display

    Streaming exists for the voice demo: it lets the browser begin speaking the
    first sentence while the rest is still generating, which cuts perceived
    latency roughly in half on local hardware.
    """
    session_id = request.session_id or uuid.uuid4().hex

    async def events():
        try:
            async for event in conversation_service.handle_streaming(
                session_id, request.message, channel=request.channel,
            ):
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
        except Exception as exc:
            log.exception("Streaming turn failed")
            payload = {
                "code": "stream_failed",
                "message": "Something went wrong while answering. Please try again.",
                "detail": type(exc).__name__,
            }
            yield f"event: error\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Prevents proxies (and Vite's dev proxy) from buffering the stream.
            "X-Accel-Buffering": "no",
        },
    )
