"""Redacted per-service SQLite audit logging with 30-day retention purge."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared.sqlite_utils import open_connection
from shared.time_utils import get_timezone, now_rfc3339, parse_rfc3339

_SENSITIVE_KEYS = {
    "qrtoken",
    "qr_token",
    "password",
    "passwordhash",
    "password_hash",
    "sessiontoken",
    "session_token",
    "authorization",
    "servicesecret",
    "service_secret",
    "token",
    "secret",
    "name",
    "email",
    "studentname",
    "student_name",
    "recipient",
    "deliverydetails",
    "delivery_details",
    "x_service_secret",
}

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _scrub_string(value: str) -> str:
    scrubbed = _BEARER_RE.sub("Bearer [REDACTED]", value)
    scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", scrubbed)
    return scrubbed


def redact_payload(value: Any) -> Any:
    """Recursively redact sensitive keys, bearer tokens, and emails from structured data."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            norm = _normalize_key(str(key))
            if norm in _SENSITIVE_KEYS or norm.replace("_", "") in _SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = redact_payload(item)
        return sanitized
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


class AuditLogger:
    """Isolated audit log storage for an individual service."""

    def __init__(self, db_path: Path | str, service_name: str, tz_offset_hours: int = 8) -> None:
        self.db_path = Path(db_path)
        self.service_name = service_name
        self.tz_offset_hours = tz_offset_hours
        self._init_schema()

    def _init_schema(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    log_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error_code TEXT,
                    actor_id TEXT,
                    event_id TEXT,
                    registration_id TEXT,
                    details_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs (timestamp);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_corr ON audit_logs (correlation_id);"
            )
        finally:
            conn.close()

    def record(
        self,
        *,
        correlation_id: str,
        category: str,
        action: str,
        outcome: str,
        error_code: str | None = None,
        actor_id: str | None = None,
        event_id: str | None = None,
        registration_id: str | None = None,
        details: dict[str, Any] | None = None,
        timestamp_override: str | None = None,
    ) -> str:
        """Persist a redacted security or integration audit record."""
        log_id = f"LOG-{uuid.uuid4().hex[:12]}"
        ts = timestamp_override or now_rfc3339(self.tz_offset_hours)
        safe_details = redact_payload(details or {})
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    log_id, timestamp, correlation_id, service, category, action,
                    outcome, error_code, actor_id, event_id, registration_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    log_id,
                    ts,
                    correlation_id or "corr-unknown",
                    self.service_name,
                    category,
                    action,
                    outcome,
                    error_code,
                    actor_id,
                    event_id,
                    registration_id,
                    json.dumps(safe_details, separators=(",", ":")),
                ),
            )
            return log_id
        finally:
            conn.close()

    def list_logs(
        self,
        limit: int = 200,
        category: str | None = None,
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read audit entries in reverse chronological order (no modify/delete API exposed)."""
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT log_id, timestamp, correlation_id, service, category, action, "
            "outcome, error_code, actor_id, event_id, registration_id, details_json "
            f"FROM audit_logs {where} ORDER BY timestamp DESC, rowid DESC LIMIT ?;"
        )
        params.append(limit)
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [
                {
                    "logId": row["log_id"],
                    "timestamp": row["timestamp"],
                    "correlationId": row["correlation_id"],
                    "service": row["service"],
                    "category": row["category"],
                    "action": row["action"],
                    "outcome": row["outcome"],
                    "errorCode": row["error_code"],
                    "actorId": row["actor_id"],
                    "eventId": row["event_id"],
                    "registrationId": row["registration_id"],
                    "details": json.loads(row["details_json"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def purge_expired_logs(
        self,
        retention_days: int = 30,
        now: datetime | None = None,
    ) -> int:
        """Delete log entries older than retention_days (Proposal Section 6 retention policy)."""
        reference = now or datetime.now(get_timezone(self.tz_offset_hours))
        cutoff = reference - timedelta(days=retention_days)
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute("SELECT log_id, timestamp FROM audit_logs;").fetchall()
            to_delete: list[str] = []
            for row in rows:
                try:
                    entry_dt = parse_rfc3339(row["timestamp"])
                    if entry_dt < cutoff:
                        to_delete.append(row["log_id"])
                except ValueError:
                    continue
            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                conn.execute(
                    f"DELETE FROM audit_logs WHERE log_id IN ({placeholders});",
                    tuple(to_delete),
                )
            return len(to_delete)
        finally:
            conn.close()
