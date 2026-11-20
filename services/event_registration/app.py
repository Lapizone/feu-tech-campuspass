"""Event Registration FastAPI microservice."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.event_registration.directory_client import DirectoryClient
from services.event_registration.notification_client import NotificationDispatchClient
from services.event_registration.repository import SqliteEventRegistrationRepository
from shared.audit import AuditLogger
from shared.config import Settings, load_settings
from shared.errors import ServiceError, get_correlation_id, register_exception_handlers
from shared.middleware import CorrelationIdMiddleware, JsonBoundaryMiddleware
from shared.security import (
    ALL_ROLES,
    ROLE_ADMINISTRATOR,
    ROLE_CHECK_IN_STAFF,
    ROLE_ORGANIZER,
    ROLE_STUDENT,
    authenticate_service_request,
    require_user_permission,
)
from shared.time_utils import is_valid_rfc3339_with_offset
from shared.tls import build_client_ssl_context


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CreateEventRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    eventId: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    capacity: int = Field(ge=1)
    eligibleStatuses: list[str] = Field(default_factory=lambda: ["ENROLLED"], min_length=1)
    startsAt: str | None = None

    @field_validator("eventId")
    @classmethod
    def check_event_id(cls, value: str | None) -> str | None:
        if value is not None:
            clean = value.strip()
            if not clean or not _SAFE_ID_RE.match(clean):
                raise ValueError("eventId must contain only letters, digits, hyphens, or underscores.")
            return clean
        return value

    @field_validator("title")
    @classmethod
    def check_title_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank.")
        return value

    @field_validator("eligibleStatuses")
    @classmethod
    def check_statuses_non_blank(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values]
        if any(not v for v in cleaned):
            raise ValueError("eligibleStatuses items must not be empty.")
        return cleaned

    @field_validator("startsAt")
    @classmethod
    def check_rfc3339(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_rfc3339_with_offset(value):
            raise ValueError("startsAt must be RFC 3339 with explicit timezone offset.")
        return value


class UpdateEventRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    eligibleStatuses: list[str] | None = Field(default=None, min_length=1)
    startsAt: str | None = None

    @field_validator("title")
    @classmethod
    def check_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title must not be blank.")
        return value

    @field_validator("eligibleStatuses")
    @classmethod
    def check_statuses_non_blank(cls, values: list[str] | None) -> list[str] | None:
        if values is not None:
            cleaned = [v.strip() for v in values]
            if any(not v for v in cleaned):
                raise ValueError("eligibleStatuses items must not be empty.")
            return cleaned
        return values

    @field_validator("startsAt")
    @classmethod
    def check_rfc3339(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_rfc3339_with_offset(value):
            raise ValueError("startsAt must be RFC 3339 with explicit timezone offset.")
        return value


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    studentId: str = Field(min_length=1)

    @field_validator("studentId")
    @classmethod
    def non_blank_student(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("studentId must not be empty.")
        return clean


class ValidateRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    eventId: str = Field(min_length=1)
    qrToken: str = Field(min_length=1)

    @field_validator("eventId", "qrToken")
    @classmethod
    def non_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Field must not be empty.")
        return clean


def create_app(
    settings: Settings | None = None,
    *,
    repository: SqliteEventRegistrationRepository | None = None,
    directory_http_client: httpx.AsyncClient | None = None,
    notification_http_client: httpx.AsyncClient | None = None,
    enable_notifications: bool = True,
) -> FastAPI:
    """Create and configure the Event Registration FastAPI microservice."""
    cfg = settings or load_settings()
    repo = repository or SqliteEventRegistrationRepository(
        cfg.event_registration_db,
        token_hmac_key=cfg.token_hmac_key,
        token_fernet_key=cfg.token_fernet_key,
        tz_offset_hours=cfg.tz_offset_hours,
    )
    audit = AuditLogger(cfg.event_registration_db, "event-registration", cfg.tz_offset_hours)

    owns_dir_client = directory_http_client is None
    ssl_verify = (
        build_client_ssl_context(cfg.tls_ca_cert_path)
        if (directory_http_client is None or (enable_notifications and notification_http_client is None))
        else True
    )
    dir_http = directory_http_client or httpx.AsyncClient(
        base_url=cfg.student_directory_url,
        verify=ssl_verify,
        trust_env=False,
    )
    dir_client = DirectoryClient(dir_http, cfg)

    notif_client: NotificationDispatchClient | None = None
    owns_notif_client = False
    if notification_http_client is not None:
        notif_client = NotificationDispatchClient(
            notification_http_client,
            cfg,
            caller_service_id="event-registration",
            caller_service_secret=cfg.event_registration_service_secret,
            audit=audit,
            failure_recorder=repo.record_failed_notification,
        )
    elif enable_notifications and cfg.notification_service_url:
        owns_notif_client = True
        notif_http = httpx.AsyncClient(
            base_url=cfg.notification_service_url,
            verify=ssl_verify,
            trust_env=False,
        )
        notif_client = NotificationDispatchClient(
            notif_http,
            cfg,
            caller_service_id="event-registration",
            caller_service_secret=cfg.event_registration_service_secret,
            audit=audit,
            failure_recorder=repo.record_failed_notification,
        )

    app = FastAPI(title="Event Registration Service", version="1.0.0")
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, audit=audit)

    @app.on_event("shutdown")
    async def shutdown_clients() -> None:
        if owns_dir_client:
            await dir_http.aclose()
        if owns_notif_client and notif_client is not None:
            await notif_client.resilient.client.aclose()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "event-registration"}

    @app.get("/api/v1/events")
    async def list_events(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, ALL_ROLES, cid)
        return {"events": repo.list_events()}

    @app.get("/api/v1/events/{eventId}")
    async def get_event(eventId: str, request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, ALL_ROLES, cid)
        ev = repo.get_event(eventId.strip())
        if not ev:
            raise ServiceError(404, "EVENT_NOT_FOUND", f"Event '{eventId}' not found.", cid)
        return ev

    @app.post("/api/v1/events")
    async def create_event(body: CreateEventRequest, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        actor = require_user_permission(
            user_ctx, {ROLE_ORGANIZER, ROLE_ADMINISTRATOR}, cid
        )
        ev = repo.create_event(
            event_id=body.eventId,
            title=body.title,
            description=body.description,
            capacity=body.capacity,
            eligible_statuses=body.eligibleStatuses,
            starts_at=body.startsAt,
        )
        audit.record(
            correlation_id=cid,
            category="Event and registration activity",
            action="CREATE_EVENT",
            outcome="SUCCESS",
            actor_id=actor["accountId"],
            event_id=ev["eventId"],
            details={"capacity": ev["capacity"]},
        )
        return JSONResponse(status_code=201, content=ev)

    @app.put("/api/v1/events/{eventId}")
    async def update_event(
        eventId: str, body: UpdateEventRequest, request: Request
    ) -> dict[str, Any]:
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
        ev = repo.update_event(
            clean_event,
            title=body.title,
            description=body.description,
            capacity=body.capacity,
            eligible_statuses=body.eligibleStatuses,
            starts_at=body.startsAt,
        )
        audit.record(
            correlation_id=cid,
            category="Event and registration activity",
            action="UPDATE_EVENT",
            outcome="SUCCESS",
            actor_id=actor["accountId"],
            event_id=clean_event,
            details={"capacity": ev["capacity"]},
        )
        return ev

    @app.post("/api/v1/events/{eventId}/registrations")
    async def register_student(
        eventId: str, body: RegistrationRequest, request: Request
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        started = time.monotonic()
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        actor = require_user_permission(user_ctx, {ROLE_STUDENT}, cid)

        clean_event = eventId.strip()
        if not clean_event:
            raise ServiceError(422, "INVALID_PAYLOAD", "eventId must not be empty.", cid)

        if actor.get("studentId") != body.studentId:
            audit.record(
                correlation_id=cid,
                category="Authentication and authorization",
                action="REGISTER_STUDENT",
                outcome="DENIED",
                error_code="FORBIDDEN",
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"submittedStudentId": body.studentId},
            )
            raise ServiceError(
                403,
                "FORBIDDEN",
                "Student identifiers submitted during registration must match the authenticated student account.",
                cid,
            )

        ev = repo.get_event(clean_event)
        if not ev:
            raise ServiceError(404, "EVENT_NOT_FOUND", f"Event '{clean_event}' not found.", cid)

        def _log_dir_retry(attempt: int, reason: str) -> None:
            audit.record(
                correlation_id=cid,
                category="Integration failures",
                action="DIRECTORY_RETRY",
                outcome="RETRYING",
                error_code="SERVICE_UNAVAILABLE",
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"attempt": attempt, "reason": reason},
            )

        try:
            eligibility = await dir_client.check_eligibility(
                body.studentId,
                clean_event,
                cid,
                overall_deadline_sec=cfg.registration_deadline_sec,
                on_retry=_log_dir_retry,
            )
        except ServiceError as exc:
            audit.record(
                correlation_id=cid,
                category="Event and registration activity",
                action="REGISTER_STUDENT",
                outcome="REJECTED",
                error_code=exc.code,
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"studentId": body.studentId},
            )
            raise

        if not eligibility["eligible"]:
            audit.record(
                correlation_id=cid,
                category="Event and registration activity",
                action="REGISTER_STUDENT",
                outcome="REJECTED",
                error_code="INELIGIBLE",
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"studentId": body.studentId},
            )
            raise ServiceError(
                422,
                "INELIGIBLE",
                f"Student '{body.studentId}' is not eligible for event '{clean_event}'.",
                cid,
            )

        try:
            created = repo.create_registration(
                event_id=clean_event,
                student_id=body.studentId,
                recipient_email=eligibility["email"],
                enrollment_status=eligibility["enrollmentStatus"],
            )
        except ServiceError as exc:
            audit.record(
                correlation_id=cid,
                category="Event and registration activity",
                action="REGISTER_STUDENT",
                outcome="REJECTED",
                error_code=exc.code,
                actor_id=actor["accountId"],
                event_id=clean_event,
                details={"studentId": body.studentId},
            )
            raise

        audit.record(
            correlation_id=cid,
            category="Event and registration activity",
            action="REGISTER_STUDENT",
            outcome="SUCCESS",
            actor_id=actor["accountId"],
            event_id=clean_event,
            registration_id=created["registrationId"],
            details={"studentId": body.studentId},
        )

        # Notification failure must never reverse an already committed registration.
        if notif_client is not None:
            elapsed = time.monotonic() - started
            remaining = max(0.1, cfg.registration_deadline_sec - elapsed)
            await notif_client.send_notification(
                notification_type="REGISTRATION_CONFIRMED",
                recipient=eligibility["email"],
                event_id=clean_event,
                registration_id=created["registrationId"],
                delivery_details={"qrToken": created["qrToken"]},
                correlation_id=cid,
                overall_deadline_sec=remaining,
            )

        return JSONResponse(status_code=201, content=created)

    @app.post("/api/v1/registrations/validate")
    async def validate_registration_token(
        body: ValidateRequest, request: Request
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        caller_service, user_ctx = authenticate_service_request(
            request,
            allowed_services={"attendance-service", "gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        if caller_service == "gateway":
            require_user_permission(
                user_ctx,
                {ROLE_CHECK_IN_STAFF, ROLE_ADMINISTRATOR},
                cid,
                event_id=body.eventId,
                check_event_assignment=True,
            )

        result = repo.validate_token(body.eventId, body.qrToken)
        audit.record(
            correlation_id=cid,
            category="Attendance activity",
            action="VALIDATE_TOKEN",
            outcome="VALID" if result["valid"] else "INVALID",
            actor_id=caller_service,
            event_id=body.eventId,
            registration_id=result["registrationId"],
            details={"valid": result["valid"]},
        )
        if caller_service == "gateway":
            return {
                "registrationId": result["registrationId"],
                "eventId": result["eventId"],
                "status": result["status"],
                "valid": result["valid"],
            }
        return result

    @app.get("/api/v1/events/{eventId}/registrations")
    async def list_event_registrations(
        eventId: str,
        request: Request,
        status: str | None = Query(default=None),
        studentId: str | None = Query(default=None),
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        caller_service, user_ctx = authenticate_service_request(
            request,
            allowed_services={"attendance-service", "gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        clean_event = eventId.strip()
        if not clean_event:
            raise ServiceError(422, "INVALID_PAYLOAD", "eventId must not be empty.", cid)
        if status is not None and status != "CONFIRMED":
            raise ServiceError(
                422,
                "INVALID_PAYLOAD",
                "Only status=CONFIRMED is supported on this endpoint.",
                cid,
            )

        clean_student = studentId.strip() if studentId is not None else None
        if caller_service == "gateway":
            actor = require_user_permission(
                user_ctx,
                {ROLE_ORGANIZER, ROLE_ADMINISTRATOR, ROLE_STUDENT},
                cid,
            )
            if actor["role"] == ROLE_ORGANIZER:
                require_user_permission(
                    actor,
                    {ROLE_ORGANIZER},
                    cid,
                    event_id=clean_event,
                    check_event_assignment=True,
                )
            elif actor["role"] == ROLE_STUDENT:
                if not clean_student or clean_student != actor.get("studentId"):
                    raise ServiceError(
                        403,
                        "FORBIDDEN",
                        "Students may only query their own registration status.",
                        cid,
                    )

        regs = repo.list_confirmed_registrations(clean_event, student_id=clean_student)
        return {"eventId": clean_event, "registrations": regs}

    @app.get("/api/v1/my/registrations")
    async def get_my_registrations(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        actor = require_user_permission(user_ctx, {ROLE_STUDENT}, cid)
        sid = actor.get("studentId")
        if not sid:
            raise ServiceError(403, "FORBIDDEN", "Account has no studentId bound.", cid)
        return {"studentId": sid, "registrations": repo.list_student_registrations(sid)}

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
        return {"service": "event-registration", "failedNotifications": repo.list_failed_notifications()}

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
        return {"service": "event-registration", "logs": audit.list_logs(limit=limit)}

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
        return {"service": "event-registration", "deletedCount": deleted}

    app.state.settings = cfg
    app.state.repo = repo
    app.state.audit = audit
    return app
