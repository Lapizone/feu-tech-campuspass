"""API Gateway, Session Auth, RBAC Enforcement, and Web UI Host."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.gateway.repository import SqliteGatewayAuthRepository
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
    build_service_headers,
    require_user_permission,
)
from shared.tls import build_client_ssl_context

_COOKIE_NAME = "campus_session"
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class LoginRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    @field_validator("username", "password")
    @classmethod
    def check_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank.")
        return value


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: str = Field(min_length=1)
    studentId: str | None = None
    assignedEventIds: list[str] = Field(default_factory=list)

    @field_validator("username", "password", "role")
    @classmethod
    def check_non_blank_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be blank.")
        return value

    @field_validator("assignedEventIds")
    @classmethod
    def check_assigned_events(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values]
        if any(not v for v in cleaned):
            raise ValueError("assignedEventIds entries must not be blank.")
        return cleaned


class UpdateAssignmentsRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    role: str | None = None
    assignedEventIds: list[str] | None = None

    @field_validator("assignedEventIds")
    @classmethod
    def check_assigned_events(cls, values: list[str] | None) -> list[str] | None:
        if values is not None:
            cleaned = [v.strip() for v in values]
            if any(not v for v in cleaned):
                raise ValueError("assignedEventIds entries must not be blank.")
            return cleaned
        return values


def create_app(
    settings: Settings | None = None,
    *,
    repository: SqliteGatewayAuthRepository | None = None,
    directory_http_client: httpx.AsyncClient | None = None,
    registration_http_client: httpx.AsyncClient | None = None,
    attendance_http_client: httpx.AsyncClient | None = None,
    notification_http_client: httpx.AsyncClient | None = None,
    web_dir: Path | str | None = None,
) -> FastAPI:
    """Create and configure the API Gateway & Authentication FastAPI service."""
    cfg = settings or load_settings()
    repo = repository or SqliteGatewayAuthRepository(
        cfg.gateway_auth_db, tz_offset_hours=cfg.tz_offset_hours
    )
    audit = AuditLogger(cfg.gateway_auth_db, "gateway", cfg.tz_offset_hours)

    owns_dir = directory_http_client is None
    owns_reg = registration_http_client is None
    owns_att = attendance_http_client is None
    owns_notif = notification_http_client is None

    ssl_verify = (
        build_client_ssl_context(cfg.tls_ca_cert_path)
        if (owns_dir or owns_reg or owns_att or owns_notif)
        else True
    )

    dir_http = directory_http_client or httpx.AsyncClient(
        base_url=cfg.student_directory_url, verify=ssl_verify, trust_env=False
    )
    reg_http = registration_http_client or httpx.AsyncClient(
        base_url=cfg.event_registration_url, verify=ssl_verify, trust_env=False
    )
    att_http = attendance_http_client or httpx.AsyncClient(
        base_url=cfg.attendance_service_url, verify=ssl_verify, trust_env=False
    )
    notif_http = notification_http_client or httpx.AsyncClient(
        base_url=cfg.notification_service_url, verify=ssl_verify, trust_env=False
    )

    backend_clients: dict[str, httpx.AsyncClient] = {
        "student-directory": dir_http,
        "event-registration": reg_http,
        "attendance-service": att_http,
        "notification-service": notif_http,
    }

    app = FastAPI(title="Campus Event API Gateway", version="1.0.0")
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app, audit=audit)

    static_root = Path(web_dir) if web_dir else (_ROOT_DIR / "web")
    if static_root.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_ui():
            index_file = static_root / "index.html"
            if index_file.is_file():
                return FileResponse(str(index_file))
            return JSONResponse({"status": "ok", "service": "gateway"})

    @app.on_event("shutdown")
    async def shutdown_clients() -> None:
        if owns_dir:
            await dir_http.aclose()
        if owns_reg:
            await reg_http.aclose()
        if owns_att:
            await att_http.aclose()
        if owns_notif:
            await notif_http.aclose()

    def _extract_session_token(request: Request) -> str | None:
        auth = request.headers.get("Authorization", "").strip()
        if auth.startswith("Bearer "):
            token = auth[len("Bearer ") :].strip()
            if token:
                return token
        cookie_val = request.cookies.get(_COOKIE_NAME, "").strip()
        return cookie_val or None

    def _require_session(request: Request) -> tuple[str, dict[str, Any]]:
        cid = get_correlation_id(request)
        token = _extract_session_token(request)
        if not token:
            raise ServiceError(
                401,
                "UNAUTHORIZED",
                "Missing session credentials.",
                cid,
            )
        user_ctx = repo.validate_session(token)
        if not user_ctx:
            raise ServiceError(
                401,
                "UNAUTHORIZED",
                "Session credentials are invalid or expired.",
                cid,
            )
        return token, user_ctx

    async def _parse_json_object(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        data = await request.json()
        if not isinstance(data, dict):
            raise ServiceError(
                422,
                "INVALID_PAYLOAD",
                "Request JSON body must be an object.",
                cid,
            )
        return data

    async def _forward(
        client: httpx.AsyncClient,
        service_name: str,
        method: str,
        path: str,
        request: Request,
        user_ctx: dict[str, Any],
        *,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        headers = build_service_headers(
            service_id="gateway",
            service_secret=cfg.gateway_service_secret,
            correlation_id=cid,
            user_ctx=user_ctx,
            context_hmac_key=cfg.context_hmac_key,
        )
        wait_timeout = timeout_sec if timeout_sec is not None else cfg.client_wait_timeout_sec
        try:
            resp = await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
                timeout=wait_timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            audit.record(
                correlation_id=cid,
                category="Integration failures",
                action="GATEWAY_PROXY",
                outcome="UNAVAILABLE",
                error_code="SERVICE_UNAVAILABLE",
                actor_id=user_ctx["accountId"],
                details={"targetService": service_name, "path": path, "error": exc.__class__.__name__},
            )
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                f"{service_name} is unreachable or timed out.",
                cid,
            ) from exc

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                f"{service_name} returned an invalid non-JSON response.",
                cid,
            ) from exc

        return JSONResponse(
            status_code=resp.status_code,
            content=payload,
            headers={"X-Correlation-Id": cid},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "gateway"}

    # --- Authentication Endpoints ---

    @app.post("/api/v1/auth/login")
    async def login(body: LoginRequest, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        session = await asyncio.to_thread(
            repo.authenticate_and_create_session, body.username, body.password
        )
        if not session:
            audit.record(
                correlation_id=cid,
                category="Authentication and authorization",
                action="LOGIN",
                outcome="FAILURE",
                error_code="UNAUTHORIZED",
                details={"username": body.username},
            )
            raise ServiceError(
                401,
                "UNAUTHORIZED",
                "Invalid username or password.",
                cid,
            )

        audit.record(
            correlation_id=cid,
            category="Authentication and authorization",
            action="LOGIN",
            outcome="SUCCESS",
            actor_id=session["user"]["accountId"],
            details={"role": session["user"]["role"]},
        )
        response = JSONResponse(status_code=200, content=session)
        response.set_cookie(
            key=_COOKIE_NAME,
            value=session["sessionToken"],
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=8 * 3600,
        )
        return response

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        token, user_ctx = _require_session(request)
        repo.revoke_session(token)
        audit.record(
            correlation_id=cid,
            category="Authentication and authorization",
            action="LOGOUT",
            outcome="SUCCESS",
            actor_id=user_ctx["accountId"],
        )
        response = JSONResponse(status_code=200, content={"loggedOut": True})
        response.delete_cookie(_COOKIE_NAME)
        return response

    @app.get("/api/v1/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        _, user_ctx = _require_session(request)
        return user_ctx

    # --- Student Directory (Administrator only through Gateway) ---

    @app.get("/api/v1/students/{studentId}/eligibility")
    async def proxy_eligibility(
        studentId: str, request: Request, eventId: str = Query(default="")
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return await _forward(
            dir_http,
            "student-directory",
            "GET",
            f"/api/v1/students/{studentId}/eligibility",
            request,
            user_ctx,
            params={"eventId": eventId},
        )

    # --- Events & Registration Endpoints ---

    @app.get("/api/v1/events")
    async def proxy_list_events(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, ALL_ROLES, cid)
        return await _forward(
            reg_http, "event-registration", "GET", "/api/v1/events", request, user_ctx
        )

    @app.get("/api/v1/events/{eventId}")
    async def proxy_get_event(eventId: str, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, ALL_ROLES, cid)
        return await _forward(
            reg_http,
            "event-registration",
            "GET",
            f"/api/v1/events/{eventId}",
            request,
            user_ctx,
        )

    @app.post("/api/v1/events")
    async def proxy_create_event(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ORGANIZER, ROLE_ADMINISTRATOR}, cid)
        body = await _parse_json_object(request)
        resp = await _forward(
            reg_http,
            "event-registration",
            "POST",
            "/api/v1/events",
            request,
            user_ctx,
            json_body=body,
        )
        if resp.status_code == 201 and user_ctx["role"] == ROLE_ORGANIZER:
            import json as _json

            created = _json.loads(resp.body.decode("utf-8"))
            if isinstance(created, dict) and created.get("eventId"):
                repo.assign_event_to_account(user_ctx["accountId"], created["eventId"])
        return resp

    @app.put("/api/v1/events/{eventId}")
    async def proxy_update_event(eventId: str, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(
            user_ctx,
            {ROLE_ORGANIZER, ROLE_ADMINISTRATOR},
            cid,
            event_id=eventId.strip(),
            check_event_assignment=True,
        )
        body = await _parse_json_object(request)
        return await _forward(
            reg_http,
            "event-registration",
            "PUT",
            f"/api/v1/events/{eventId}",
            request,
            user_ctx,
            json_body=body,
        )

    @app.post("/api/v1/events/{eventId}/registrations")
    async def proxy_register_student(eventId: str, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_STUDENT}, cid)
        body = await _parse_json_object(request)
        submitted = body.get("studentId")
        if (
            isinstance(submitted, str)
            and submitted.strip()
            and submitted.strip() != user_ctx.get("studentId")
        ):
            raise ServiceError(
                403,
                "FORBIDDEN",
                "Student identifiers submitted during registration must match the authenticated student account.",
                cid,
            )
        return await _forward(
            reg_http,
            "event-registration",
            "POST",
            f"/api/v1/events/{eventId}/registrations",
            request,
            user_ctx,
            json_body=body,
        )

    @app.get("/api/v1/events/{eventId}/registrations")
    async def proxy_list_registrations(
        eventId: str,
        request: Request,
        status: str | None = Query(default=None),
        studentId: str | None = Query(default=None),
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        clean_event = eventId.strip()
        require_user_permission(
            user_ctx, {ROLE_ORGANIZER, ROLE_ADMINISTRATOR, ROLE_STUDENT}, cid
        )
        if user_ctx["role"] == ROLE_ORGANIZER:
            require_user_permission(
                user_ctx,
                {ROLE_ORGANIZER},
                cid,
                event_id=clean_event,
                check_event_assignment=True,
            )
        elif user_ctx["role"] == ROLE_STUDENT:
            if not studentId or studentId.strip() != user_ctx.get("studentId"):
                raise ServiceError(
                    403,
                    "FORBIDDEN",
                    "Students may only inspect their own registration records.",
                    cid,
                )

        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        if studentId is not None:
            params["studentId"] = studentId
        return await _forward(
            reg_http,
            "event-registration",
            "GET",
            f"/api/v1/events/{eventId}/registrations",
            request,
            user_ctx,
            params=params,
        )

    @app.get("/api/v1/my/registrations")
    async def proxy_my_registrations(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_STUDENT}, cid)
        return await _forward(
            reg_http,
            "event-registration",
            "GET",
            "/api/v1/my/registrations",
            request,
            user_ctx,
        )

    @app.post("/api/v1/registrations/validate")
    async def proxy_validate_token(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        body = await _parse_json_object(request)
        target_event = body.get("eventId")
        require_user_permission(
            user_ctx,
            {ROLE_CHECK_IN_STAFF, ROLE_ADMINISTRATOR},
            cid,
            event_id=target_event if isinstance(target_event, str) else None,
            check_event_assignment=isinstance(target_event, str) and bool(target_event.strip()),
        )
        return await _forward(
            reg_http,
            "event-registration",
            "POST",
            "/api/v1/registrations/validate",
            request,
            user_ctx,
            json_body=body,
        )

    # --- Attendance Endpoints ---

    @app.post("/api/v1/events/{eventId}/check-ins")
    async def proxy_check_in(eventId: str, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(
            user_ctx,
            {ROLE_CHECK_IN_STAFF, ROLE_ADMINISTRATOR},
            cid,
            event_id=eventId.strip(),
            check_event_assignment=True,
        )
        body = await _parse_json_object(request)
        return await _forward(
            att_http,
            "attendance-service",
            "POST",
            f"/api/v1/events/{eventId}/check-ins",
            request,
            user_ctx,
            json_body=body,
        )

    @app.get("/api/v1/events/{eventId}/registrations/{registrationId}/attendance")
    async def proxy_attendance_status(
        eventId: str, registrationId: str, request: Request
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(
            user_ctx,
            {ROLE_CHECK_IN_STAFF, ROLE_ORGANIZER, ROLE_ADMINISTRATOR},
            cid,
            event_id=eventId.strip(),
            check_event_assignment=True,
        )
        return await _forward(
            att_http,
            "attendance-service",
            "GET",
            f"/api/v1/events/{eventId}/registrations/{registrationId}/attendance",
            request,
            user_ctx,
        )

    @app.get("/api/v1/events/{eventId}/reports/attendance")
    async def proxy_attendance_report(eventId: str, request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(
            user_ctx,
            {ROLE_ORGANIZER, ROLE_ADMINISTRATOR},
            cid,
            event_id=eventId.strip(),
            check_event_assignment=True,
        )
        return await _forward(
            att_http,
            "attendance-service",
            "GET",
            f"/api/v1/events/{eventId}/reports/attendance",
            request,
            user_ctx,
            timeout_sec=max(cfg.client_wait_timeout_sec, cfg.reporting_deadline_sec),
        )

    # --- Notification Proxy (Administrator only via Gateway) ---

    @app.get("/api/v1/notifications")
    async def proxy_list_notifications(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return await _forward(
            notif_http,
            "notification-service",
            "GET",
            "/api/v1/notifications",
            request,
            user_ctx,
        )

    @app.post("/api/v1/notifications")
    async def proxy_send_notification(request: Request) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        body = await _parse_json_object(request)
        return await _forward(
            notif_http,
            "notification-service",
            "POST",
            "/api/v1/notifications",
            request,
            user_ctx,
            json_body=body,
        )

    # --- Administrator Account, Audit, and Health Endpoints ---

    @app.get("/api/v1/admin/accounts")
    async def list_accounts(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        return {"accounts": repo.list_accounts()}

    @app.post("/api/v1/admin/accounts")
    async def create_account(
        body: CreateAccountRequest, request: Request
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        created = repo.create_account(
            username=body.username,
            password=body.password,
            role=body.role,
            student_id=body.studentId,
            assigned_event_ids=body.assignedEventIds,
        )
        audit.record(
            correlation_id=cid,
            category="Authentication and authorization",
            action="CREATE_ACCOUNT",
            outcome="SUCCESS",
            actor_id=user_ctx["accountId"],
            details={"createdAccountId": created["accountId"], "role": created["role"]},
        )
        return JSONResponse(status_code=201, content=created)

    @app.put("/api/v1/admin/accounts/{accountId}/assignments")
    async def update_account_assignments(
        accountId: str, body: UpdateAssignmentsRequest, request: Request
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)
        updated = repo.update_assignments(
            accountId.strip(),
            role=body.role,
            assigned_event_ids=body.assignedEventIds,
        )
        audit.record(
            correlation_id=cid,
            category="Authentication and authorization",
            action="UPDATE_ASSIGNMENTS",
            outcome="SUCCESS",
            actor_id=user_ctx["accountId"],
            details={"targetAccountId": accountId.strip(), "role": updated["role"]},
        )
        return updated

    @app.get("/api/v1/admin/audit-logs")
    async def get_aggregated_audit_logs(
        request: Request, limit: int = Query(default=200, ge=1, le=1000)
    ) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)

        combined = list(audit.list_logs(limit=limit))
        headers = build_service_headers(
            service_id="gateway",
            service_secret=cfg.gateway_service_secret,
            correlation_id=cid,
            user_ctx=user_ctx,
            context_hmac_key=cfg.context_hmac_key,
        )

        async def _fetch_logs(svc_name: str, client: httpx.AsyncClient) -> tuple[str, list[dict[str, Any]] | None]:
            try:
                resp = await client.get(
                    "/api/v1/internal/audit-logs",
                    params={"limit": str(limit)},
                    headers=headers,
                    timeout=cfg.client_wait_timeout_sec,
                )
                if resp.status_code == 200:
                    return svc_name, resp.json().get("logs", [])
                return svc_name, None
            except (httpx.TimeoutException, httpx.TransportError, ValueError):
                return svc_name, None

        unavailable: list[str] = []
        for svc_name, svc_logs in await asyncio.gather(
            *(_fetch_logs(svc_name, client) for svc_name, client in backend_clients.items())
        ):
            if svc_logs is None:
                unavailable.append(svc_name)
            else:
                combined.extend(svc_logs)

        combined.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return {
            "logs": combined[:limit],
            "unavailableServices": unavailable,
        }

    @app.post("/api/v1/admin/audit-logs/purge")
    async def purge_all_audit_logs(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)

        results: dict[str, int] = {
            "gateway": audit.purge_expired_logs(cfg.audit_retention_days)
        }
        headers = build_service_headers(
            service_id="gateway",
            service_secret=cfg.gateway_service_secret,
            correlation_id=cid,
            user_ctx=user_ctx,
            context_hmac_key=cfg.context_hmac_key,
        )

        async def _purge_svc(svc_name: str, client: httpx.AsyncClient) -> tuple[str, int | None]:
            try:
                resp = await client.post(
                    "/api/v1/internal/audit-logs/purge",
                    headers=headers,
                    timeout=cfg.client_wait_timeout_sec,
                )
                if resp.status_code == 200:
                    return svc_name, int(resp.json().get("deletedCount", 0))
                return svc_name, None
            except (httpx.TimeoutException, httpx.TransportError, ValueError):
                return svc_name, -1

        for svc_name, deleted in await asyncio.gather(
            *(_purge_svc(svc_name, client) for svc_name, client in backend_clients.items())
        ):
            if deleted is not None:
                results[svc_name] = deleted
        return {"retentionDays": cfg.audit_retention_days, "deletedByService": results}

    @app.get("/api/v1/admin/failed-notifications")
    async def list_all_failed_notifications(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)

        headers = build_service_headers(
            service_id="gateway",
            service_secret=cfg.gateway_service_secret,
            correlation_id=cid,
            user_ctx=user_ctx,
            context_hmac_key=cfg.context_hmac_key,
        )

        async def _fetch_failed(svc_name: str, client: httpx.AsyncClient) -> tuple[str, list[dict[str, Any]] | None]:
            try:
                resp = await client.get(
                    "/api/v1/internal/failed-notifications",
                    headers=headers,
                    timeout=cfg.client_wait_timeout_sec,
                )
                if resp.status_code == 200:
                    return svc_name, [
                        {"service": svc_name, **item}
                        for item in resp.json().get("failedNotifications", [])
                    ]
                return svc_name, None
            except (httpx.TimeoutException, httpx.TransportError, ValueError):
                return svc_name, None

        failures: list[dict[str, Any]] = []
        unavailable: list[str] = []
        for svc_name, svc_failures in await asyncio.gather(
            _fetch_failed("event-registration", reg_http),
            _fetch_failed("attendance-service", att_http),
        ):
            if svc_failures is None:
                unavailable.append(svc_name)
            else:
                failures.extend(svc_failures)
        return {"failedNotifications": failures, "unavailableServices": unavailable}

    @app.get("/api/v1/admin/service-health")
    async def get_service_health(request: Request) -> dict[str, Any]:
        cid = get_correlation_id(request)
        _, user_ctx = _require_session(request)
        require_user_permission(user_ctx, {ROLE_ADMINISTRATOR}, cid)

        async def _probe(svc_name: str, client: httpx.AsyncClient) -> tuple[str, str]:
            try:
                resp = await client.get("/health", timeout=2.0)
                return svc_name, (
                    "ok" if resp.status_code == 200 else f"error ({resp.status_code})"
                )
            except (httpx.TimeoutException, httpx.TransportError):
                return svc_name, "unavailable"

        statuses: dict[str, str] = {"gateway": "ok"}
        for svc_name, status in await asyncio.gather(
            *(_probe(svc_name, client) for svc_name, client in backend_clients.items())
        ):
            statuses[svc_name] = status
        return {"services": statuses}

    app.state.settings = cfg
    app.state.repo = repo
    app.state.audit = audit
    return app
