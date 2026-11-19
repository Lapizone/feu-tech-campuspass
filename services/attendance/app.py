"""Attendance FastAPI microservice."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.attendance.registration_client import RegistrationIntegrationClient
from services.attendance.repository import SqliteAttendanceRepository
from services.event_registration.notification_client import NotificationDispatchClient
from shared.audit import AuditLogger
from shared.config import Settings, load_settings
from shared.errors import ServiceError, get_correlation_id, register_exception_handlers
from shared.middleware import CorrelationIdMiddleware, JsonBoundaryMiddleware
from shared.security import (
    ROLE_ADMINISTRATOR,
    ROLE_CHECK_IN_STAFF,
    ROLE_ORGANIZER,
    authenticate_service_request,
    require_user_permission,
)
from shared.tls import build_client_ssl_context


class CheckInRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    qrToken: str = Field(min_length=1)

    @field_validator("qrToken")
    @classmethod
    def non_blank_token(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("qrToken must not be empty.")
        return clean


def create_app(
    settings: Settings | None = None,
    *,
    repository: SqliteAttendanceRepository | None = None,
    registration_http_client: httpx.AsyncClient | None = None,
    notification_http_client: httpx.AsyncClient | None = None,
    enable_notifications: bool = True,
) -> FastAPI:
    """Create and configure the Attendance FastAPI microservice."""
    cfg = settings or load_settings()
    repo = repository or SqliteAttendanceRepository(
        cfg.attendance_db, tz_offset_hours=cfg.tz_offset_hours
    )
    audit = AuditLogger(cfg.attendance_db, "attendance-service", cfg.tz_offset_hours)

    owns_reg_client = registration_http_client is None
    ssl_verify = (
        build_client_ssl_context(cfg.tls_ca_cert_path)
        if (registration_http_client is None or (enable_notifications and notification_http_client is None))
        else True
    )
    reg_http = registration_http_client or httpx.AsyncClient(
        base_url=cfg.event_registration_url,
        verify=ssl_verify,
    )
    reg_client = RegistrationIntegrationClient(reg_http, cfg)

    notif_client: NotificationDispatchClient | None = None
    owns_notif_client = False
    if notification_http_client is not None:
        notif_client = NotificationDispatchClient(
            notification_http_client,
            cfg,
            caller_service_id="attendance-service",
            caller_service_secret=cfg.attendance_service_secret,
            audit=audit,
            failure_recorder=repo.record_failed_notification,
        )
    elif enable_notifications and cfg.notification_service_url:
        owns_notif_client = True
        notif_http = httpx.AsyncClient(
            base_url=cfg.notification_service_url,
            verify=ssl_verify,
        )
        notif_client = NotificationDispatchClient(
            notif_http,
            cfg,
            caller_service_id="attendance-service",
            caller_service_secret=cfg.attendance_service_secret,
            audit=audit,
            failure_recorder=repo.record_failed_notification,
        )

    app = FastAPI(title="Attendance Service", version="1.0.0")
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, audit=audit)

    @app.on_event("shutdown")
    async def shutdown_clients() -> None:
        if owns_reg_client:
            await reg_http.aclose()
        if owns_notif_client and notif_client is not None:
            await notif_client.resilient.client.aclose()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "attendance-service"}

    @app.post("/api/v1/events/{eventId}/check-ins")
    async def check_in_participant(
        eventId: str, body: CheckInRequest, request: Request
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        started = time.monotonic()
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        clean_event = eventId.strip()
        if not clean_event:
            raise ServiceError(422, "INVALID_PAYLOAD", "eventId must not be empty.", cid)

        actor = require_user_permission(
            user_ctx,
            {ROLE_CHECK_IN_STAFF, ROLE_ADMINISTRATOR},
            cid,
            event_id=clean_event,
            check_event_assignment=True,
        )

        def _log_reg_retry(attempt: int, reason: str) -> None:
            audit.record(
                correlation_id=cid,
                category="Integration failures",
                action="REGISTRATION_VALIDATE_RETRY",
                outcome="RETRYING",
                error_code="SERVICE_UNAVAILABLE",
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"attempt": attempt, "reason": reason},
            )

        try:
            validated = await reg_client.validate_token(
                clean_event,
                body.qrToken,
                cid,
                overall_deadline_sec=cfg.checkin_deadline_sec,
                on_retry=_log_reg_retry,
            )
        except ServiceError as exc:
            audit.record(
                correlation_id=cid,
                category="Integration failures",
                action="VALIDATE_TOKEN_CALL",
                outcome="UNAVAILABLE",
                error_code=exc.code,
                actor_id=actor["accountId"],
                event_id=clean_event,
            )
            raise

        is_valid = (
            validated.get("valid") is True
            and validated.get("eventId") == clean_event
            and validated.get("status") == "CONFIRMED"
            and isinstance(validated.get("registrationId"), str)
            and len(validated["registrationId"]) > 0
        )
        if not is_valid:
            audit.record(
                correlation_id=cid,
                category="Attendance activity",
                action="CHECK_IN",
                outcome="REJECTED",
                error_code="INVALID_TOKEN",
                actor_id=actor["accountId"],
                event_id=clean_event,
            )
            raise ServiceError(
                404,
                "INVALID_TOKEN",
                "QR token is invalid, unconfirmed, or belongs to a different event.",
                cid,
            )

        reg_id = validated["registrationId"]
        try:
            recorded = repo.record_check_in(
                event_id=clean_event,
                registration_id=reg_id,
            )
        except ServiceError as exc:
            audit.record(
                correlation_id=cid,
                category="Attendance activity",
                action="CHECK_IN",
                outcome="DUPLICATE",
                error_code=exc.code,
                actor_id=actor["accountId"],
                event_id=clean_event,
                registration_id=reg_id,
            )
            raise

        audit.record(
            correlation_id=cid,
            category="Attendance activity",
            action="CHECK_IN",
            outcome="SUCCESS",
            actor_id=actor["accountId"],
            event_id=clean_event,
            registration_id=reg_id,
        )

        # Non-blocking notification dispatch after saved attendance record.
        recipient = validated.get("recipient")
        if notif_client is not None and isinstance(recipient, str) and recipient.strip():
            elapsed = time.monotonic() - started
            remaining = max(0.1, cfg.checkin_deadline_sec - elapsed)
            await notif_client.send_notification(
                notification_type="CHECK_IN_CONFIRMED",
                recipient=recipient,
                event_id=clean_event,
                registration_id=reg_id,
                delivery_details={"checkInTime": recorded["checkInTime"]},
                correlation_id=cid,
                overall_deadline_sec=remaining,
            )

        return JSONResponse(status_code=201, content=recorded)

    @app.get("/api/v1/events/{eventId}/registrations/{registrationId}/attendance")
    async def get_attendance_status(
        eventId: str, registrationId: str, request: Request
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        clean_event = eventId.strip()
        clean_reg = registrationId.strip()
        if not clean_event or not clean_reg:
            raise ServiceError(
                422,
                "INVALID_PAYLOAD",
                "eventId and registrationId must be nonempty.",
                cid,
            )
        actor = require_user_permission(
            user_ctx,
            {ROLE_CHECK_IN_STAFF, ROLE_ORGANIZER, ROLE_ADMINISTRATOR},
            cid,
            event_id=clean_event,
            check_event_assignment=True,
        )
        status = repo.get_attendance_status(clean_event, clean_reg)
        audit.record(
            correlation_id=cid,
            category="Attendance activity",
            action="QUERY_ATTENDANCE_STATUS",
            outcome="CHECKED_IN" if status["checkedIn"] else "ABSENT",
            actor_id=actor["accountId"],
            event_id=clean_event,
            registration_id=clean_reg,
        )
        return status

    @app.get("/api/v1/events/{eventId}/reports/attendance")
    async def get_attendance_report(eventId: str, request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        clean_event = eventId.strip()
        if not clean_event:
            raise ServiceError(422, "INVALID_PAYLOAD", "eventId must not be empty.", cid)

        actor = require_user_permission(
            user_ctx,
            {ROLE_ORGANIZER, ROLE_ADMINISTRATOR},
            cid,
            event_id=clean_event,
            check_event_assignment=True,
        )

        def _log_report_retry(attempt: int, reason: str) -> None:
            audit.record(
                correlation_id=cid,
                category="Integration failures",
                action="REPORT_SOURCE_RETRY",
                outcome="RETRYING",
                error_code="SERVICE_UNAVAILABLE",
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"attempt": attempt, "reason": reason},
            )

        try:
            reg_data = await reg_client.list_confirmed_registrations(
                clean_event,
                cid,
                overall_deadline_sec=cfg.reporting_deadline_sec,
                on_retry=_log_report_retry,
            )
        except ServiceError as exc:
            audit.record(
                correlation_id=cid,
                category="Notifications and reporting",
                action="GENERATE_ATTENDANCE_REPORT",
                outcome="FAILED",
                error_code=exc.code,
                actor_id=actor["accountId"],
                event_id=clean_event,
            )
            raise

        attendance_by_reg = repo.list_event_attendance(clean_event)
        participants: list[dict[str, Any]] = []
        checked_in_count = 0

        for reg in reg_data["registrations"]:
            reg_id = reg["registrationId"]
            att = attendance_by_reg.get(reg_id)
            is_checked_in = att is not None
            if is_checked_in:
                checked_in_count += 1
            participants.append(
                {
                    "registrationId": reg_id,
                    "studentId": reg["studentId"],
                    "registrationStatus": reg["status"],
                    "checkedIn": is_checked_in,
                    "checkInTime": att["checkInTime"] if att else None,
                }
            )

        registered_count = len(participants)
        not_checked_in_count = registered_count - checked_in_count

        audit.record(
            correlation_id=cid,
            category="Notifications and reporting",
            action="GENERATE_ATTENDANCE_REPORT",
            outcome="SUCCESS",
            actor_id=actor["accountId"],
            event_id=clean_event,
            details={
                "registeredCount": registered_count,
                "checkedInCount": checked_in_count,
                "notCheckedInCount": not_checked_in_count,
            },
        )
        return {
            "eventId": clean_event,
            "participants": participants,
            "registeredCount": registered_count,
            "checkedInCount": checked_in_count,
            "notCheckedInCount": not_checked_in_count,
        }

    @app.get("/api/v1/internal/failed-notifications")
    async def get_failed_notifications(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"service": "attendance-service", "failedNotifications": repo.list_failed_notifications()}

    @app.get("/api/v1/internal/audit-logs")
    async def get_audit_logs(
        request: Request, limit: int = Query(default=200, ge=1, le=1000)
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"service": "attendance-service", "logs": audit.list_logs(limit=limit)}

    @app.post("/api/v1/internal/audit-logs/purge")
    async def purge_audit_logs(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        deleted = audit.purge_expired_logs(cfg.audit_retention_days)
        return {"service": "attendance-service", "deletedCount": deleted}

    app.state.settings = cfg
    app.state.repo = repo
    app.state.audit = audit
    return app
