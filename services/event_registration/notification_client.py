"""Shared non-reversing Notification Service client for Registration & Attendance."""

from __future__ import annotations

from typing import Any

import httpx

from shared.audit import AuditLogger
from shared.config import Settings
from shared.http_client import DependencyUnavailableError, ResilientServiceClient
from shared.schemas import validate_dependency_payload
from shared.security import build_service_headers


class NotificationDispatchClient:
    """Submits notification requests with 3s timeout and up to 2 retries.

    Notification failures NEVER roll back an already committed registration or
    check-in; exhausted retries are persisted for authorized review.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        settings: Settings,
        *,
        caller_service_id: str,
        caller_service_secret: str,
        audit: AuditLogger,
        failure_recorder,
        resilient_client: ResilientServiceClient | None = None,
    ) -> None:
        self.settings = settings
        self.caller_service_id = caller_service_id
        self.caller_service_secret = caller_service_secret
        self.audit = audit
        self.failure_recorder = failure_recorder
        self.resilient = resilient_client or ResilientServiceClient(
            http_client,
            retry_delays=settings.retry_delays_sec,
            max_retries=settings.retry_max_attempts,
        )

    async def send_notification(
        self,
        *,
        notification_type: str,
        recipient: str,
        event_id: str,
        registration_id: str,
        delivery_details: dict[str, Any],
        correlation_id: str,
        overall_deadline_sec: float | None = None,
    ) -> dict[str, Any] | None:
        headers = build_service_headers(
            service_id=self.caller_service_id,
            service_secret=self.caller_service_secret,
            correlation_id=correlation_id,
        )
        payload = {
            "type": notification_type,
            "recipient": recipient,
            "eventId": event_id,
            "registrationId": registration_id,
            "deliveryDetails": delivery_details,
        }

        def _log_retry(attempt_num: int, reason: str) -> None:
            self.audit.record(
                correlation_id=correlation_id,
                category="Integration failures",
                action="NOTIFICATION_RETRY",
                outcome="RETRYING",
                error_code="SERVICE_UNAVAILABLE",
                actor_id=self.caller_service_id,
                event_id=event_id,
                registration_id=registration_id,
                details={"attempt": attempt_num, "reason": reason, "type": notification_type},
            )

        deadline = (
            overall_deadline_sec
            if overall_deadline_sec is not None
            else (
                self.settings.notification_timeout_sec * (1 + self.settings.retry_max_attempts)
                + sum(self.settings.retry_delays_sec)
            )
        )

        try:
            resp = await self.resilient.request_with_retry(
                "POST",
                "/api/v1/notifications",
                headers=headers,
                json=payload,
                timeout_sec=self.settings.notification_timeout_sec,
                overall_deadline_sec=deadline,
                allow_retry=True,
                on_retry=_log_retry,
            )
        except DependencyUnavailableError as exc:
            self._record_exhausted(
                notification_type=notification_type,
                event_id=event_id,
                registration_id=registration_id,
                correlation_id=correlation_id,
                error_message=str(exc),
            )
            return None

        if resp.status_code != 202:
            self._record_exhausted(
                notification_type=notification_type,
                event_id=event_id,
                registration_id=registration_id,
                correlation_id=correlation_id,
                error_message=f"Notification Service returned HTTP {resp.status_code}.",
            )
            return None

        try:
            data = validate_dependency_payload(
                "notification_response", resp.json(), correlation_id
            )
        except Exception as exc:
            self._record_exhausted(
                notification_type=notification_type,
                event_id=event_id,
                registration_id=registration_id,
                correlation_id=correlation_id,
                error_message=f"Notification response validation failed: {exc}",
            )
            return None

        self.audit.record(
            correlation_id=correlation_id,
            category="Notifications and reporting",
            action="DISPATCH_NOTIFICATION",
            outcome="ACCEPTED",
            actor_id=self.caller_service_id,
            event_id=event_id,
            registration_id=registration_id,
            details={"type": notification_type, "notificationId": data["notificationId"]},
        )
        return data

    def _record_exhausted(
        self,
        *,
        notification_type: str,
        event_id: str,
        registration_id: str,
        correlation_id: str,
        error_message: str,
    ) -> None:
        self.failure_recorder(
            notification_type=notification_type,
            event_id=event_id,
            registration_id=registration_id,
            correlation_id=correlation_id,
            error_message=error_message,
        )
        self.audit.record(
            correlation_id=correlation_id,
            category="Integration failures",
            action="NOTIFICATION_EXHAUSTED",
            outcome="FAILED_NON_BLOCKING",
            error_code="SERVICE_UNAVAILABLE",
            actor_id=self.caller_service_id,
            event_id=event_id,
            registration_id=registration_id,
            details={"type": notification_type, "errorMessage": error_message},
        )
