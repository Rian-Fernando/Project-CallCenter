"""GoGov integration abstraction (§21).

IMPORTANT — NO INVENTED API
    GoGov does not publish a public API specification. This module therefore
    defines the shape the Village *would* need, backed by an explicitly labeled
    mock. No endpoint URL, auth scheme, or payload here is claimed to match a
    real GoGov service, because that information is not publicly available.

    Every response carries `"mode": "mock"` and `"is_live": false`, and the UI
    renders a MOCK GOV SERVICE badge. Nothing in this file may be presented to
    a resident as a real Village service request.

TO GO LIVE
    1. Obtain API documentation and credentials from GoGov under the Village's
       existing contract.
    2. Implement LiveGoGovService against the documented endpoints.
    3. Register it in `get_gogov_service()` and set GOGOV_MODE=live.
    See docs/GOGOV_INTEGRATION.md for the full checklist.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.routing.departments import get_departments

log = logging.getLogger(__name__)


class GoGovService(ABC):
    """The interface the application depends on."""

    mode: str = "unknown"
    is_live: bool = False

    @abstractmethod
    async def search_faqs(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        ...

    @abstractmethod
    async def create_request(
        self, *, department: str, summary: str, details: str = "",
        contact: str | None = None,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    async def get_request_status(self, request_id: str) -> dict[str, Any]:
        ...

    @abstractmethod
    async def generate_service_link(
        self, *, department: str, service: str | None = None,
    ) -> dict[str, Any]:
        ...


class MockGoGovService(GoGovService):
    """Demonstration implementation. Returns clearly labeled fake data."""

    mode = "mock"
    is_live = False

    _DISCLAIMER = (
        "MOCK GOV SERVICE — this is simulated data for demonstration only. "
        "No request was submitted to any Village system."
    )

    def _envelope(self, **payload) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "is_live": self.is_live,
            "disclaimer": self._DISCLAIMER,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }

    async def search_faqs(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """Deliberately returns no results.

        Returning invented FAQ text would put fabricated municipal answers into
        the pipeline — precisely what this system is built to prevent. An empty
        result set is the honest mock.
        """
        return self._envelope(
            query=query, results=[], count=0,
            note=("The mock GoGov service returns no FAQ results by design. "
                  "Answers come from the Village knowledge base instead."),
        )

    async def create_request(
        self, *, department: str, summary: str, details: str = "",
        contact: str | None = None,
    ) -> dict[str, Any]:
        request_id = f"MOCK-{uuid.uuid4().hex[:8].upper()}"
        return self._envelope(
            request_id=request_id,
            status="simulated",
            department=department,
            department_name=get_departments().name_of(department),
            summary=summary,
            details=details,
            # Contact info is echoed but never stored (§17).
            contact_provided=bool(contact),
            submitted=False,
            note="No service request was created. This id exists only in this response.",
        )

    async def get_request_status(self, request_id: str) -> dict[str, Any]:
        return self._envelope(
            request_id=request_id,
            status="unknown",
            note=("Status lookup requires a live GoGov connection. "
                  "See docs/GOGOV_INTEGRATION.md."),
        )

    async def generate_service_link(
        self, *, department: str, service: str | None = None,
    ) -> dict[str, Any]:
        """Point at the official Village website rather than a fabricated URL."""
        return self._envelope(
            department=department,
            department_name=get_departments().name_of(department),
            service=service,
            url=settings.village_base_url,
            note=("Links to the Village's public website. A live integration "
                  "would deep-link into the GoGov service catalog."),
        )


class LiveGoGovService(GoGovService):
    """Placeholder for a real integration. Intentionally not implemented."""

    mode = "live"
    is_live = True

    _MESSAGE = (
        "The live GoGov integration is not implemented. GoGov does not publish "
        "a public API specification, so no endpoints are assumed here. "
        "See docs/GOGOV_INTEGRATION.md for what the Village must obtain from "
        "GoGov before this can be built."
    )

    async def search_faqs(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)

    async def create_request(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)

    async def get_request_status(self, request_id: str) -> dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)

    async def generate_service_link(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)


def get_gogov_service() -> GoGovService:
    if settings.gogov_mode.strip().lower() == "live":
        log.warning("GOGOV_MODE=live but the live integration is a stub.")
        return LiveGoGovService()
    return MockGoGovService()
