"""Student registration consumer SDK with C2 schema validation and safe reconciliation."""

from __future__ import annotations

from typing import Any

import httpx

from shared.errors import ServiceError
from shared.schemas import validate_dependency_payload


def _safe_json(resp: httpx.Response, correlation_id: str) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"message": str(data)}
    except ValueError:
        return {
            "code": "SERVICE_UNAVAILABLE",
            "message": f"Unexpected non-JSON response (HTTP {resp.status_code}).",
            "correlationId": correlation_id,
        }


class RegistrationClient:
    """Consumer-side client enforcing C2 response schema and reconcile-before-retry."""

    def __init__(self, http_client: httpx.AsyncClient, timeout_sec: float = 8.0) -> None:
        self.http_client = http_client
        self.timeout_sec = timeout_sec

    async def register(
        self,
        event_id: str,
        student_id: str,
        *,
        headers: dict[str, str],
        correlation_id: str = "corr-reg-client",
    ) -> dict[str, Any]:
        """Submit registration and validate response against C2 registration_response schema."""
        req_headers = {"Content-Type": "application/json", **headers}
        try:
            resp = await self.http_client.post(
                f"/api/v1/events/{event_id}/registrations",
                json={"studentId": student_id},
                headers=req_headers,
                timeout=self.timeout_sec,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Registration outcome is unverified due to network/timeout failure.",
                correlation_id,
            ) from exc

        if resp.status_code == 201:
            return validate_dependency_payload(
                "registration_response", _safe_json(resp, correlation_id), correlation_id
            )

        body = _safe_json(resp, correlation_id)
        raise ServiceError(
            resp.status_code,
            body.get("code", "SERVICE_UNAVAILABLE"),
            body.get("message", "Registration failed."),
            body.get("correlationId", correlation_id),
        )

    async def register_with_safe_reconciliation(
        self,
        event_id: str,
        student_id: str,
        *,
        headers: dict[str, str],
        correlation_id: str = "corr-reg-reconcile",
    ) -> dict[str, Any]:
        """Reconcile authoritative registration list before retrying an uncertain write."""
        try:
            res = await self.register(
                event_id, student_id, headers=headers, correlation_id=correlation_id
            )
            return {"outcome": "CONFIRMED", "retried": False, "registration": res}
        except ServiceError as exc:
            if exc.status_code != 503:
                raise

        # Uncertain/503 outcome: check authoritative list before resubmitting.
        try:
            check = await self.http_client.get(
                f"/api/v1/events/{event_id}/registrations",
                params={"status": "CONFIRMED", "studentId": student_id},
                headers=headers,
                timeout=self.timeout_sec,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return {"outcome": "UNVERIFIED", "retried": False, "registration": None}

        if check.status_code != 200:
            return {"outcome": "UNVERIFIED", "retried": False, "registration": None}

        validated_list = validate_dependency_payload(
            "registrations_list_response", _safe_json(check, correlation_id), correlation_id
        )
        matches = [
            r for r in validated_list["registrations"] if r["studentId"] == student_id
        ]
        if matches:
            return {
                "outcome": "ALREADY_CONFIRMED",
                "retried": False,
                "registration": matches[0],
            }

        # Absence confirmed -> safe to retry once.
        retried = await self.register(
            event_id, student_id, headers=headers, correlation_id=correlation_id
        )
        return {"outcome": "CONFIRMED", "retried": True, "registration": retried}
