"""Student Directory FastAPI microservice."""

from __future__ import annotations

from fastapi import FastAPI, Query, Request

from services.student_directory.repository import (
    LegacyDirectoryAdapter,
    SqliteStudentDirectoryRepository,
)
from shared.audit import AuditLogger
from shared.config import Settings, load_settings
from shared.errors import ServiceError, get_correlation_id, register_exception_handlers
from shared.middleware import CorrelationIdMiddleware, JsonBoundaryMiddleware
from shared.security import (
    ROLE_ADMINISTRATOR,
    authenticate_service_request,
    require_user_permission,
)


def create_app(
    settings: Settings | None = None,
    repository: LegacyDirectoryAdapter | None = None,
) -> FastAPI:
    """Create and configure the Student Directory FastAPI app."""
    cfg = settings or load_settings()
    repo = repository or SqliteStudentDirectoryRepository(cfg.student_directory_db)
    audit = AuditLogger(cfg.student_directory_db, "student-directory", cfg.tz_offset_hours)

    app = FastAPI(title="Student Directory Service", version="1.0.0")
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "student-directory"}

    @app.get("/api/v1/students/{studentId}/eligibility")
    async def get_student_eligibility(
        studentId: str,
        request: Request,
        eventId: str = Query(default=""),
    ) -> dict[str, object]:
        cid = get_correlation_id(request)
        caller_service, user_ctx = authenticate_service_request(
            request,
            allowed_services={"event-registration", "gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        if caller_service == "gateway":
            require_user_permission(
                user_ctx,
                allowed_roles={ROLE_ADMINISTRATOR},
                correlation_id=cid,
            )

        clean_student = studentId.strip()
        clean_event = eventId.strip()
        if not clean_student or not clean_event:
            audit.record(
                correlation_id=cid,
                category="Authentication and authorization",
                action="VERIFY_ELIGIBILITY",
                outcome="REJECTED",
                error_code="INVALID_PAYLOAD",
                actor_id=caller_service,
                event_id=clean_event or None,
            )
            raise ServiceError(
                422,
                "INVALID_PAYLOAD",
                "Both studentId and eventId must be nonempty strings.",
                cid,
            )

        record = repo.get_student_eligibility(clean_student, clean_event)
        if record is None:
            audit.record(
                correlation_id=cid,
                category="Event and registration activity",
                action="VERIFY_ELIGIBILITY",
                outcome="NOT_FOUND",
                error_code="STUDENT_NOT_FOUND",
                actor_id=caller_service,
                event_id=clean_event,
                details={"studentId": clean_student},
            )
            raise ServiceError(
                404,
                "STUDENT_NOT_FOUND",
                f"Student '{clean_student}' was not found in the directory.",
                cid,
            )

        audit.record(
            correlation_id=cid,
            category="Event and registration activity",
            action="VERIFY_ELIGIBILITY",
            outcome="SUCCESS",
            actor_id=caller_service,
            event_id=clean_event,
            details={
                "studentId": clean_student,
                "eligible": record["eligible"],
                "enrollmentStatus": record["enrollmentStatus"],
            },
        )
        return record

    @app.get("/api/v1/internal/audit-logs")
    async def list_audit_logs(request: Request, limit: int = 200) -> dict[str, object]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"service": "student-directory", "logs": audit.list_logs(limit=limit)}

    @app.post("/api/v1/internal/audit-logs/purge")
    async def purge_audit_logs(request: Request) -> dict[str, object]:
        cid = get_correlation_id(request)
        _, user_ctx = authenticate_service_request(
            request,
            allowed_services={"gateway"},
            service_secrets=cfg.service_secrets,
            context_hmac_key=cfg.context_hmac_key,
        )
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        deleted = audit.purge_expired_logs(cfg.audit_retention_days)
        return {"service": "student-directory", "deletedCount": deleted}

    app.state.settings = cfg
    app.state.audit = audit
    return app
