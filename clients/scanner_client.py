"""Check-in scanner SDK implementing Proposal Section 5 & FP 2 reconciliation."""

from __future__ import annotations

from typing import Any

import httpx

from shared.schemas import validate_dependency_payload


class ScannerClient:
    """Scanner client that pre-validates tokens, retains registrationId, and reconciles before retry."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        session_token: str | None = None,
        timeout_sec: float = 8.0,
    ) -> None:
        self.http_client = http_client
        self.session_token = session_token
        self.timeout_sec = timeout_sec

    def _headers(self, correlation_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-Id": correlation_id,
        }
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        return headers

    async def scan_and_check_in(
        self,
        event_id: str,
        qr_token: str,
        *,
        correlation_id: str = "corr-scanner",
    ) -> dict[str, Any]:
        """Step 1: validate token to retain registrationId; Step 2: submit check-in once."""
        headers = self._headers(correlation_id)

        # Pre-validate via Registration validation endpoint (Page 10)
        try:
            val_resp = await self.http_client.post(
                "/api/v1/registrations/validate",
                json={"eventId": event_id, "qrToken": qr_token},
                headers=headers,
                timeout=self.timeout_sec,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return {
                "state": "UNVERIFIED",
                "statusCode": 503,
                "registrationId": None,
                "checkIn": None,
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": f"Token validation unreachable ({exc.__class__.__name__}).",
                    "correlationId": correlation_id,
                },
            }

        if val_resp.status_code != 200:
            return {
                "state": "UNVERIFIED",
                "statusCode": val_resp.status_code,
                "registrationId": None,
                "checkIn": None,
                "error": val_resp.json(),
            }

        val_data = validate_dependency_payload(
            "validate_response", val_resp.json(), correlation_id
        )
        if not val_data["valid"] or not val_data["registrationId"]:
            return {
                "state": "INVALID_TOKEN",
                "statusCode": 404,
                "registrationId": None,
                "checkIn": None,
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "QR token is invalid or belongs to a different event.",
                    "correlationId": correlation_id,
                },
            }

        reg_id = val_data["registrationId"]
        return await self._submit_checkin_once(
            event_id,
            qr_token=qr_token,
            registration_id=reg_id,
            correlation_id=correlation_id,
        )

    async def _submit_checkin_once(
        self,
        event_id: str,
        *,
        qr_token: str,
        registration_id: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        headers = self._headers(correlation_id)
        try:
            resp = await self.http_client.post(
                f"/api/v1/events/{event_id}/check-ins",
                json={"qrToken": qr_token},
                headers=headers,
                timeout=self.timeout_sec,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return {
                "state": "UNVERIFIED",
                "statusCode": 503,
                "registrationId": registration_id,
                "checkIn": None,
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": f"Check-in outcome is unverified ({exc.__class__.__name__}).",
                    "correlationId": correlation_id,
                },
            }

        body = resp.json()
        if resp.status_code == 201:
            validated = validate_dependency_payload("checkin_response", body, correlation_id)
            return {
                "state": "CHECKED_IN",
                "statusCode": 201,
                "registrationId": validated["registrationId"],
                "checkIn": validated,
                "error": None,
            }
        if resp.status_code == 409:
            return {
                "state": "ALREADY_CHECKED_IN",
                "statusCode": 409,
                "registrationId": registration_id,
                "checkIn": None,
                "error": body,
            }
        if resp.status_code == 404:
            return {
                "state": "INVALID_TOKEN",
                "statusCode": 404,
                "registrationId": registration_id,
                "checkIn": None,
                "error": body,
            }
        return {
            "state": "UNVERIFIED",
            "statusCode": resp.status_code,
            "registrationId": registration_id,
            "checkIn": None,
            "error": body,
        }

    async def reconcile_and_maybe_retry(
        self,
        event_id: str,
        *,
        qr_token: str,
        registration_id: str,
        correlation_id: str = "corr-reconcile",
    ) -> dict[str, Any]:
        """Query authoritative attendance status; display existing or retry only if confirmed absent."""
        headers = self._headers(correlation_id)
        try:
            status_resp = await self.http_client.get(
                f"/api/v1/events/{event_id}/registrations/{registration_id}/attendance",
                headers=headers,
                timeout=self.timeout_sec,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return {
                "state": "UNVERIFIED",
                "retried": False,
                "registrationId": registration_id,
                "checkIn": None,
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": f"Attendance status reconciliation unavailable ({exc.__class__.__name__}).",
                    "correlationId": correlation_id,
                },
            }

        if status_resp.status_code != 200:
            return {
                "state": "UNVERIFIED",
                "retried": False,
                "registrationId": registration_id,
                "checkIn": None,
                "error": status_resp.json(),
            }

        status_data = validate_dependency_payload(
            "attendance_status_response", status_resp.json(), correlation_id
        )
        if status_data["checkedIn"]:
            return {
                "state": "CHECKED_IN",
                "retried": False,
                "registrationId": registration_id,
                "checkIn": {
                    "registrationId": registration_id,
                    "attendanceStatus": "CHECKED_IN",
                    "checkInTime": status_data["checkInTime"],
                },
                "error": None,
            }

        # Absence confirmed -> controlled single retry
        retry_res = await self._submit_checkin_once(
            event_id,
            qr_token=qr_token,
            registration_id=registration_id,
            correlation_id=correlation_id,
        )
        return {**retry_res, "retried": True}
