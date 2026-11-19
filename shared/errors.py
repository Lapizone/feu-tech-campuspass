"""Standardized error envelope {code, message, correlationId} and FastAPI handlers."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def error_payload(code: str, message: str, correlation_id: str) -> dict[str, str]:
    """Build the contract-mandated error response body."""
    return {
        "code": code,
        "message": message,
        "correlationId": correlation_id or "corr-unknown",
    }


class ServiceError(Exception):
    """Domain or boundary error carrying HTTP status, contract error code, and message."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.correlation_id = correlation_id

    def to_dict(self, fallback_correlation_id: str = "corr-unknown") -> dict[str, str]:
        """Serialize to the uniform {code, message, correlationId} structure."""
        return error_payload(
            code=self.code,
            message=self.message,
            correlation_id=self.correlation_id or fallback_correlation_id,
        )


def get_correlation_id(request: Request) -> str:
    """Extract the correlationId attached by CorrelationIdMiddleware."""
    cid = getattr(request.state, "correlation_id", None)
    if isinstance(cid, str) and cid:
        return cid
    header_cid = request.headers.get("X-Correlation-Id", "").strip()
    return header_cid or "corr-unknown"


def register_exception_handlers(app: FastAPI) -> None:
    """Attach uniform error envelope handlers to a FastAPI instance."""

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
        cid = get_correlation_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(fallback_correlation_id=cid),
            headers={"X-Correlation-Id": cid},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        cid = get_correlation_id(request)
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        detail = first.get("msg", "Invalid payload.")
        msg = f"Invalid field '{loc}': {detail}" if loc else f"Invalid payload: {detail}"
        return JSONResponse(
            status_code=422,
            content=error_payload("INVALID_PAYLOAD", msg, cid),
            headers={"X-Correlation-Id": cid},
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        cid = get_correlation_id(request)
        status_code = exc.status_code
        code_map = {
            400: "MALFORMED_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            415: "UNSUPPORTED_MEDIA_TYPE",
            422: "INVALID_PAYLOAD",
            503: "SERVICE_UNAVAILABLE",
        }
        code = code_map.get(status_code, "HTTP_ERROR")
        message = str(exc.detail) if exc.detail else "Request could not be completed."
        return JSONResponse(
            status_code=status_code,
            content=error_payload(code, message, cid),
            headers={"X-Correlation-Id": cid},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        cid = get_correlation_id(request)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "INTERNAL_ERROR",
                "An unexpected internal error occurred.",
                cid,
            ),
            headers={"X-Correlation-Id": cid},
        )
