"""Notification FastAPI microservice."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.notification.repository import SqliteNotificationRepository
from shared.audit import AuditLogger
from shared.config import Settings, load_settings
from shared.errors import ServiceError, get_correlation_id, register_exception_handlers
from shared.middleware import CorrelationIdMiddleware, JsonBoundaryMiddleware
from shared.security import (
    ROLE_ADMINISTRATOR,
    authenticate_service_request,
    require_user_permission,
)


class NotificationRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal[
        "REGISTRATION_CONFIRMED",
        "QR_ISSUED",
        "REMINDER",
        "CANCELLATION",
        "CHECK_IN_CONFIRMED",
    ]
    recipient: str = Field(min_length=1)
    eventId: str = Field(min_length=1)
    registrationId: str = Field(min_length=1)
    deliveryDetails: dict[str, Any]

    @field_validator("recipient", "eventId", "registrationId")
    @classmethod
    def check_non_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Field must not be blank.")
        return clean


def create_app(
    settings: Settings | None = None,
    *,
    repository: SqliteNotificationRepository | None = None,
) -> FastAPI:
    """Create and configure the Notification FastAPI microservice."""
    cfg = settings or load_settings()
    repo = repository or SqliteNotificationRepository(
        cfg.notification_db, tz_offset_hours=cfg.tz_offset_hours
    )
    audit = AuditLogger(cfg.notification_db, "notification-service", cfg.tz_offset_hours)

    app = FastAPI(title="Notification Service", version="1.0.0")
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, audit=audit)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "notification-service"}

    @app.post("/api/v1/notifications")
    async def accept_notification(
        body: NotificationRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        caller_service, user_ctx = authenticate_service_request(
            request,
            allowed_services={"event-registration", "attendance-service", "gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        if caller_service == "gateway":
            require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)

        result = repo.accept_notification(
            notification_type=body.type,
            recipient=body.recipient,
            event_id=body.eventId,
            registration_id=body.registrationId,
            delivery_details=body.deliveryDetails,
        )
        if not result["duplicate"]:
            background_tasks.add_task(
                repo.complete_simulated_delivery, result["notificationId"]
            )

        audit.record(
            correlation_id=cid,
            category="Notifications and reporting",
            action=(
                "DUPLICATE_NOTIFICATION_ACCEPTANCE"
                if result["duplicate"]
                else "ACCEPT_NOTIFICATION"
            ),
            outcome="ACCEPTED",
            actor_id=caller_service,
            event_id=body.eventId,
            registration_id=body.registrationId,
            details={
                "type": body.type,
                "notificationId": result["notificationId"],
                "duplicate": result["duplicate"],
            },
        )
        return JSONResponse(
            status_code=202,
            content={
                "notificationId": result["notificationId"],
                "status": "ACCEPTED",
            },
        )

    @app.get("/api/v1/notifications")
    async def list_notifications(request: Request, limit: int = 100) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"notifications": repo.list_notifications(limit=limit)}

    @app.get("/api/v1/notifications/{notificationId}")
    async def get_notification(notificationId: str, request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        item = repo.get_notification(notificationId.strip())
        if not item:
            raise ServiceError(
                404,
                "NOTIFICATION_NOT_FOUND",
                f"Notification '{notificationId}' was not found.",
                cid,
            )
        return item

    @app.get("/api/v1/internal/audit-logs")
    async def get_audit_logs(request: Request, limit: int = 200) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"service": "notification-service", "logs": audit.list_logs(limit=limit)}

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
        return {"service": "notification-service", "deletedCount": deleted}

    app.state.settings = cfg
    app.state.repo = repo
    app.state.audit = audit
    return app
