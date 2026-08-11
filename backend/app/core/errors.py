"""Error types and handlers.

Design rule (§24): residents and admins never see a stack trace. Every failure
surfaces as a calm, actionable message plus a stable `code`; the full detail
goes to the developer log only.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for all expected, handled failures."""

    code = "internal_error"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    user_message = "Something went wrong on our end. Please try again."

    def __init__(self, detail: str | None = None, *, user_message: str | None = None):
        super().__init__(detail or self.user_message)
        self.detail = detail or ""
        if user_message:
            self.user_message = user_message


class ServiceUnavailableError(AppError):
    """A local dependency (Ollama, Qdrant, Whisper, Piper) is not reachable."""

    code = "service_unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    user_message = "A required service isn't running right now."

    def __init__(self, service: str, detail: str = "", *, hint: str = ""):
        self.service = service
        self.hint = hint
        super().__init__(
            detail,
            user_message=f"The {service} service isn't available right now.",
        )


class LLMError(AppError):
    code = "llm_error"
    http_status = status.HTTP_502_BAD_GATEWAY
    user_message = "The language model could not complete this request."


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    http_status = status.HTTP_504_GATEWAY_TIMEOUT
    user_message = "The language model took too long to respond."


class TranscriptionError(AppError):
    code = "transcription_failed"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    user_message = "I couldn't understand that audio. Could you try again?"


class SynthesisError(AppError):
    code = "synthesis_failed"
    http_status = status.HTTP_502_BAD_GATEWAY
    user_message = "I couldn't generate audio for that response."


class KnowledgeBaseError(AppError):
    code = "knowledge_base_error"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    user_message = "The knowledge base isn't available right now."


class NotFoundError(AppError):
    code = "not_found"
    http_status = status.HTTP_404_NOT_FOUND
    user_message = "That item could not be found."


class ValidationError(AppError):
    code = "invalid_request"
    http_status = status.HTTP_400_BAD_REQUEST
    user_message = "That request wasn't valid."


def _payload(code: str, message: str, ref: str, **extra) -> dict:
    body = {"error": {"code": code, "message": message, "reference": ref}}
    body["error"].update(extra)
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        ref = uuid.uuid4().hex[:8]
        log.warning(
            "AppError[%s] %s on %s %s: %s",
            ref, exc.code, request.method, request.url.path, exc.detail or exc,
        )
        extra = {}
        if isinstance(exc, ServiceUnavailableError):
            extra = {"service": exc.service, "hint": exc.hint}
        return JSONResponse(
            status_code=exc.http_status,
            content=_payload(exc.code, exc.user_message, ref, **extra),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        ref = uuid.uuid4().hex[:8]
        log.info("Validation[%s] on %s: %s", ref, request.url.path, exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload(
                "invalid_request",
                "That request was missing or malformed information.",
                ref,
                fields=[".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()],
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        ref = uuid.uuid4().hex[:8]
        # Full traceback to the developer log; never to the client.
        log.exception("Unhandled[%s] on %s %s", ref, request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload(
                "internal_error",
                "Something went wrong on our end. Please try again.",
                ref,
            ),
        )
