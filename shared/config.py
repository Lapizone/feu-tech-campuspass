"""Central runtime configuration loaded from environment and optional secrets file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    """Immutable configuration shared across services and clients."""

    app_env: str
    tz_offset_hours: int
    gateway_host: str
    gateway_port: int
    student_directory_url: str
    event_registration_url: str
    attendance_service_url: str
    notification_service_url: str
    tls_ca_cert_path: str
    tls_server_cert_path: str
    tls_server_key_path: str
    student_directory_db: str
    event_registration_db: str
    attendance_db: str
    notification_db: str
    gateway_auth_db: str
    internal_eligibility_timeout_sec: float
    internal_validate_timeout_sec: float
    registration_deadline_sec: float
    checkin_deadline_sec: float
    client_wait_timeout_sec: float
    reporting_deadline_sec: float
    notification_timeout_sec: float
    retry_max_attempts: int
    retry_delays_sec: tuple[float, ...]
    audit_retention_days: int
    gateway_service_secret: str
    event_registration_service_secret: str
    attendance_service_secret: str
    token_hmac_key: str
    token_fernet_key: str
    context_hmac_key: str
    session_secret: str

    @property
    def service_secrets(self) -> dict[str, str]:
        """Return mapping of authorized caller service IDs to their secrets."""
        return {
            "gateway": self.gateway_service_secret,
            "event-registration": self.event_registration_service_secret,
            "attendance-service": self.attendance_service_secret,
        }


def _parse_delays(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return (1.0, 2.0)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(float(p) for p in parts)


def load_settings(
    env: dict[str, str] | None = None,
    secrets_file: Path | str | None = Path("runtime/secrets.env"),
) -> Settings:
    """Load Settings from explicit env mapping, secrets file, or os.environ."""
    merged: dict[str, str] = {}
    if env is None:
        if secrets_file is not None:
            merged.update(parse_env_file(Path(secrets_file)))
        merged.update(os.environ)
    else:
        merged.update(env)

    def get(key: str, default: str) -> str:
        val = merged.get(key)
        return val if val not in (None, "") else default

    default_fernet = Fernet.generate_key().decode("ascii")
    return Settings(
        app_env=get("APP_ENV", "prototype"),
        tz_offset_hours=int(get("TZ_OFFSET_HOURS", "8")),
        gateway_host=get("GATEWAY_HOST", "127.0.0.1"),
        gateway_port=int(get("GATEWAY_PORT", "8440")),
        student_directory_url=get("STUDENT_DIRECTORY_URL", "https://127.0.0.1:8441"),
        event_registration_url=get("EVENT_REGISTRATION_URL", "https://127.0.0.1:8442"),
        attendance_service_url=get("ATTENDANCE_SERVICE_URL", "https://127.0.0.1:8443"),
        notification_service_url=get("NOTIFICATION_SERVICE_URL", "https://127.0.0.1:8444"),
        tls_ca_cert_path=get("TLS_CA_CERT_PATH", "runtime/certs/ca.crt"),
        tls_server_cert_path=get("TLS_SERVER_CERT_PATH", "runtime/certs/server.crt"),
        tls_server_key_path=get("TLS_SERVER_KEY_PATH", "runtime/certs/server.key"),
        student_directory_db=get("STUDENT_DIRECTORY_DB", "data/student_directory.db"),
        event_registration_db=get("EVENT_REGISTRATION_DB", "data/event_registration.db"),
        attendance_db=get("ATTENDANCE_DB", "data/attendance.db"),
        notification_db=get("NOTIFICATION_DB", "data/notification.db"),
        gateway_auth_db=get("GATEWAY_AUTH_DB", "data/gateway_auth.db"),
        internal_eligibility_timeout_sec=float(get("INTERNAL_ELIGIBILITY_TIMEOUT_SEC", "2.0")),
        internal_validate_timeout_sec=float(get("INTERNAL_VALIDATE_TIMEOUT_SEC", "2.0")),
        registration_deadline_sec=float(get("REGISTRATION_DEADLINE_SEC", "5.0")),
        checkin_deadline_sec=float(get("CHECKIN_DEADLINE_SEC", "5.0")),
        client_wait_timeout_sec=float(get("CLIENT_WAIT_TIMEOUT_SEC", "8.0")),
        reporting_deadline_sec=float(get("REPORTING_DEADLINE_SEC", "10.0")),
        notification_timeout_sec=float(get("NOTIFICATION_TIMEOUT_SEC", "3.0")),
        retry_max_attempts=int(get("RETRY_MAX_ATTEMPTS", "2")),
        retry_delays_sec=_parse_delays(merged.get("RETRY_DELAYS_SEC")),
        audit_retention_days=int(get("AUDIT_RETENTION_DAYS", "30")),
        gateway_service_secret=get("GATEWAY_SERVICE_SECRET", "dev-gateway-secret-ephemeral"),
        event_registration_service_secret=get("EVENT_REGISTRATION_SERVICE_SECRET", "dev-reg-secret-ephemeral"),
        attendance_service_secret=get("ATTENDANCE_SERVICE_SECRET", "dev-att-secret-ephemeral"),
        token_hmac_key=get("TOKEN_HMAC_KEY", "dev-token-hmac-key-ephemeral"),
        token_fernet_key=get("TOKEN_FERNET_KEY", default_fernet),
        context_hmac_key=get("CONTEXT_HMAC_KEY", "dev-context-hmac-key-ephemeral"),
        session_secret=get("SESSION_SECRET", "dev-session-secret-ephemeral"),
    )
