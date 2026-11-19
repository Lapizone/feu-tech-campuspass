"""Resilient HTTPS client for Student Directory eligibility verification."""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import Settings
from shared.errors import ServiceError
from shared.http_client import DependencyUnavailableError, ResilientServiceClient
from shared.schemas import validate_dependency_payload
from shared.security import build_service_headers


class DirectoryClient:
    """Calls Student Directory over HTTPS with 2s timeout, safe retries, and C1 schema validation."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        settings: Settings,
        resilient_client: ResilientServiceClient | None = None,
    ) -> None:
        self.settings = settings
        self.resilient = resilient_client or ResilientServiceClient(
            http_client,
            retry_delays=settings.retry_delays_sec,
            max_retries=settings.retry_max_attempts,
        )

    async def check_eligibility(
        self,
        student_id: str,
        event_id: str,
        correlation_id: str,
        *,
        overall_deadline_sec: float | None = None,
        on_retry=None,
    ) -> dict[str, Any]:
        """Verify student eligibility against Student Directory with strict C1 validation."""
        headers = build_service_headers(
            service_id="event-registration",
            service_secret=self.settings.event_registration_service_secret,
            correlation_id=correlation_id,
        )
        deadline = (
            overall_deadline_sec
            if overall_deadline_sec is not None
            else self.settings.registration_deadline_sec
        )
        try:
            resp = await self.resilient.request_with_retry(
                "GET",
                f"/api/v1/students/{student_id}/eligibility",
                params={"eventId": event_id},
                headers=headers,
                timeout_sec=self.settings.internal_eligibility_timeout_sec,
                overall_deadline_sec=deadline,
                allow_retry=True,
                on_retry=on_retry,
            )
        except DependencyUnavailableError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Student Directory service is unavailable or timed out.",
                correlation_id,
            ) from exc

        if resp.status_code == 404:
            raise ServiceError(
                422,
                "INELIGIBLE",
                f"Student '{student_id}' is not registered as eligible in the directory.",
                correlation_id,
            )
        if resp.status_code == 503:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Student Directory service returned 503 SERVICE_UNAVAILABLE.",
                correlation_id,
            )
        if resp.status_code != 200:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                f"Student Directory returned unexpected status {resp.status_code}.",
                correlation_id,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Student Directory returned malformed JSON.",
                correlation_id,
            ) from exc

        return validate_dependency_payload("eligibility_response", payload, correlation_id)
