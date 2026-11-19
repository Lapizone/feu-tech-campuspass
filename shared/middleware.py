"""HTTP boundary middleware: correlation ID propagation and strict JSON validation."""

from __future__ import annotations

import json
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.errors import error_payload

_BODY_METHODS = {"POST", "PUT", "PATCH"}
_EMPTY_BODY_ALLOWED_PATHS = {
    "/api/v1/auth/logout",
    "/api/v1/admin/audit-logs/purge",
}


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlationId to every request/response pair."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Correlation-Id", "").strip()
        cid = incoming if incoming else f"corr-{uuid.uuid4().hex[:12]}"
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        return response


class JsonBoundaryMiddleware(BaseHTTPMiddleware):
    """Enforce application/json (415) and well-formed JSON (400) before route handlers."""

    async def dispatch(self, request: Request, call_next):
        if request.method in _BODY_METHODS:
            cid = getattr(request.state, "correlation_id", None) or (
                request.headers.get("X-Correlation-Id", "").strip() or "corr-unknown"
            )
            body = await request.body()
            allow_empty = request.url.path in _EMPTY_BODY_ALLOWED_PATHS and len(body) == 0
            if not allow_empty:
                raw_ct = request.headers.get("content-type", "")
                media_type = raw_ct.split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    return JSONResponse(
                        status_code=415,
                        content=error_payload(
                            "UNSUPPORTED_MEDIA_TYPE",
                            "Content-Type must be application/json.",
                            cid,
                        ),
                        headers={"X-Correlation-Id": cid},
                    )
                if not body.strip():
                    return JSONResponse(
                        status_code=400,
                        content=error_payload(
                            "MALFORMED_REQUEST",
                            "Request body must be valid JSON.",
                            cid,
                        ),
                        headers={"X-Correlation-Id": cid},
                    )
                try:
                    json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return JSONResponse(
                        status_code=400,
                        content=error_payload(
                            "MALFORMED_REQUEST",
                            "Request body contains malformed JSON.",
                            cid,
                        ),
                        headers={"X-Correlation-Id": cid},
                    )
        return await call_next(request)
