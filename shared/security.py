"""Password hashing, opaque QR tokens, service-to-service auth, and RBAC helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request

from shared.errors import ServiceError, get_correlation_id

ROLE_STUDENT = "STUDENT"
ROLE_ORGANIZER = "EVENT_ORGANIZER"
ROLE_CHECK_IN_STAFF = "CHECK_IN_STAFF"
ROLE_ADMINISTRATOR = "ADMINISTRATOR"

ALL_ROLES = {
    ROLE_STUDENT,
    ROLE_ORGANIZER,
    ROLE_CHECK_IN_STAFF,
    ROLE_ADMINISTRATOR,
}


def hash_password(password: str) -> str:
    """Hash password with salted scrypt (never stored in plain text)."""
    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    dk_b64 = base64.urlsafe_b64encode(dk).decode("ascii")
    return f"scrypt${n}${r}${p}${salt_b64}${dk_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored scrypt hash using constant-time comparison."""
    try:
        scheme, n_str, r_str, p_str, salt_b64, dk_b64 = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(dk_b64.encode("ascii"))
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_str),
            r=int(r_str),
            p=int(p_str),
            dklen=len(expected),
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def generate_qr_token() -> str:
    """Generate an unpredictable opaque URL-safe QR token."""
    return secrets.token_urlsafe(32)


def compute_token_verifier(qr_token: str, hmac_key: str) -> str:
    """Compute keyed HMAC-SHA256 verifier for token lookup without storing raw plaintext."""
    return hmac.new(
        hmac_key.encode("utf-8"),
        qr_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_token_verifier(qr_token: str, expected_verifier: str, hmac_key: str) -> bool:
    """Constant-time verification of a QR token against its stored verifier."""
    actual = compute_token_verifier(qr_token, hmac_key)
    return hmac.compare_digest(actual, expected_verifier)


def encrypt_token(qr_token: str, fernet_key: str) -> str:
    """Encrypt a QR token with Fernet for retrieval strictly by its owning student."""
    f = Fernet(fernet_key.encode("ascii"))
    return f.encrypt(qr_token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str, fernet_key: str) -> str:
    """Decrypt a stored Fernet QR token."""
    try:
        f = Fernet(fernet_key.encode("ascii"))
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt stored QR token.") from exc


def sign_user_context(user_ctx: dict[str, Any], context_hmac_key: str) -> str:
    """Encode and HMAC-sign a Gateway-authenticated user context header."""
    raw = json.dumps(user_ctx, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    sig = hmac.new(
        context_hmac_key.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_user_context(header_val: str | None, context_hmac_key: str) -> dict[str, Any] | None:
    """Verify and decode signed user context header; returns None on invalid/missing."""
    if not header_val or "." not in header_val:
        return None
    try:
        payload_b64, sig = header_val.split(".", 1)
        expected_sig = hmac.new(
            context_hmac_key.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        decoded = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_service_headers(
    service_id: str,
    service_secret: str,
    correlation_id: str,
    user_ctx: dict[str, Any] | None = None,
    context_hmac_key: str | None = None,
) -> dict[str, str]:
    """Construct authenticated service-to-service request headers."""
    headers = {
        "X-Service-Id": service_id,
        "Authorization": f"Bearer {service_secret}",
        "X-Correlation-Id": correlation_id,
    }
    if user_ctx is not None and context_hmac_key:
        headers["X-User-Context"] = sign_user_context(user_ctx, context_hmac_key)
    return headers


def authenticate_service_request(
    request: Request,
    allowed_services: set[str],
    service_secrets: dict[str, str],
    context_hmac_key: str,
) -> tuple[str, dict[str, Any] | None]:
    """Authenticate an incoming request at a backend service boundary.

    Validates service credentials independently of the Gateway and parses optional
    signed user context. Rejects missing/invalid credentials with 401 and
    unauthorized caller services with 403.
    """
    cid = get_correlation_id(request)
    service_id = request.headers.get("X-Service-Id", "").strip()
    auth_header = request.headers.get("Authorization", "").strip()

    if not service_id or not auth_header.startswith("Bearer "):
        raise ServiceError(
            401,
            "UNAUTHORIZED",
            "Service credentials are missing or malformed.",
            cid,
        )

    token = auth_header[len("Bearer ") :].strip()
    expected_secret = service_secrets.get(service_id)
    if not expected_secret or not hmac.compare_digest(token, expected_secret):
        raise ServiceError(
            401,
            "UNAUTHORIZED",
            "Invalid service credentials.",
            cid,
        )

    if service_id not in allowed_services:
        raise ServiceError(
            403,
            "FORBIDDEN",
            f"Service '{service_id}' is not permitted to invoke this endpoint.",
            cid,
        )

    raw_ctx = request.headers.get("X-User-Context")
    user_ctx: dict[str, Any] | None = None
    if raw_ctx is not None:
        user_ctx = verify_user_context(raw_ctx, context_hmac_key)
        if user_ctx is None:
            raise ServiceError(
                401,
                "UNAUTHORIZED",
                "Signed user context failed verification.",
                cid,
            )

    return service_id, user_ctx


def require_user_permission(
    user_ctx: dict[str, Any] | None,
    allowed_roles: set[str],
    correlation_id: str,
    event_id: str | None = None,
    check_event_assignment: bool = False,
) -> dict[str, Any]:
    """Enforce role and assigned-event permissions at API boundaries."""
    if not user_ctx:
        raise ServiceError(
            401,
            "UNAUTHORIZED",
            "Authenticated user context is required.",
            correlation_id,
        )

    role = user_ctx.get("role")
    if role not in allowed_roles:
        raise ServiceError(
            403,
            "FORBIDDEN",
            "Authenticated account does not have permission for this operation.",
            correlation_id,
        )

    if check_event_assignment and event_id and role != ROLE_ADMINISTRATOR:
        assigned = set(user_ctx.get("assignedEventIds") or [])
        if event_id not in assigned:
            raise ServiceError(
                403,
                "FORBIDDEN",
                f"Account is not assigned to event {event_id}.",
                correlation_id,
            )

    return user_ctx
