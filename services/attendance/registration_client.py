"""Resilient HTTPS client for Attendance Service -> Event Registration interfaces."""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import Settings
from shared.errors import ServiceError
from shared.http_client import DependencyUnavailableError, ResilientServiceClient
from shared.schemas import validate_dependency_payload
from shared.security import build_service_headers


class RegistrationIntegrationClient:
    """Calls Event Registration for token validation and reporting source data."""

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

    async def validate_token(
        self,
        event_id: str,
        qr_token: str,
        correlation_id: str,
        *,
        overall_deadline_sec: float | None = None,
        on_retry=None,
    ) -> dict[str, Any]:
        """Validate token and event via Event Registration with 2s timeout & safe retries."""
        headers = build_service_headers(
            service_id="attendance-service",
            service_secret=self.settings.attendance_service_secret,
            correlation_id=correlation_id,
        )
        deadline = (
            overall_deadline_sec
            if overall_deadline_sec is not None
            else self.settings.checkin_deadline_sec
        )
        try:
            resp = await self.resilient.request_with_retry(
                "POST",
                "/api/v1/registrations/validate",
                headers=headers,
                json={"eventId": event_id, "qrToken": qr_token},
                timeout_sec=self.settings.internal_validate_timeout_sec,
                overall_deadline_sec=deadline,
                allow_retry=True,
                on_retry=on_retry,
            )
        except DependencyUnavailableError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Event Registration validation service is unavailable or timed out.",
                correlation_id,
            ) from exc

        if resp.status_code != 200:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                f"Registration validation returned unexpected status {resp.status_code}.",
                correlation_id,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Registration validation returned malformed JSON.",
                correlation_id,
            ) from exc

        return validate_dependency_payload("validate_response", payload, correlation_id)

    async def list_confirmed_registrations(
        self,
        event_id: str,
        correlation_id: str,
        *,
        overall_deadline_sec: float | None = None,
        on_retry=None,
    ) -> dict[str, Any]:
        """Fetch confirmed registrations for combined attendance report (10s deadline)."""
        headers = build_service_headers(
            service_id="attendance-service",
            service_secret=self.settings.attendance_service_secret,
            correlation_id=correlation_id,
        )
        deadline = (
            overall_deadline_sec
            if overall_deadline_sec is not None
            else self.settings.reporting_deadline_sec
        )
        per_attempt_timeout = min(deadline, 3.5)
        try:
            resp = await self.resilient.request_with_retry(
                "GET",
                f"/api/v1/events/{event_id}/registrations",
                params={"status": "CONFIRMED"},
                headers=headers,
                timeout_sec=per_attempt_timeout,
                overall_deadline_sec=deadline,
                allow_retry=True,
                on_retry=on_retry,
            )
        except DependencyUnavailableError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Combined attendance report is unavailable because registration source data could not be retrieved.",
                correlation_id,
            ) from exc

        if resp.status_code == 404:
            raise ServiceError(
                404,
                "EVENT_NOT_FOUND",
                f"Event '{event_id}' was not found.",
                correlation_id,
            )
        if resp.status_code != 200:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Combined attendance report is incomplete or unavailable from Event Registration.",
                correlation_id,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Event Registration returned malformed reporting JSON.",
                correlation_id,
            ) from exc

        return validate_dependency_payload(
            "registrations_list_response", payload, correlation_id
        )
