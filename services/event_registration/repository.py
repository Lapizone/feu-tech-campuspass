"""Event Registration repository owning events, capacities, registrations, and token verifiers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.schemas import load_fixture
from shared.security import (
    compute_token_verifier,
    decrypt_token,
    encrypt_token,
    generate_qr_token,
)
from shared.sqlite_utils import immediate_transaction, open_connection
from shared.time_utils import now_rfc3339


class SqliteEventRegistrationRepository:
    """Isolated SQLite storage for Event Registration service."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        token_hmac_key: str,
        token_fernet_key: str,
        tz_offset_hours: int = 8,
    ) -> None:
        self.db_path = Path(db_path)
        self.token_hmac_key = token_hmac_key
        self.token_fernet_key = token_fernet_key
        self.tz_offset_hours = tz_offset_hours
        self._init_schema_and_seed()

    def _init_schema_and_seed(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity >= 1),
                    eligible_statuses_json TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS registrations (
                    registration_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    student_id TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_verifier TEXT NOT NULL UNIQUE,
                    encrypted_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(event_id, student_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS failed_notifications (
                    id TEXT PRIMARY KEY,
                    notification_type TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM events;").fetchone()[0]
            if count == 0:
                seed = load_fixture("sample_seed")
                now = now_rfc3339(self.tz_offset_hours)
                for ev in seed["events"]:
                    conn.execute(
                        """
                        INSERT INTO events (
                            event_id, title, description, capacity,
                            eligible_statuses_json, starts_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            ev["eventId"],
                            ev["title"],
                            ev.get("description", ""),
                            int(ev["capacity"]),
                            json.dumps(ev.get("eligibleStatuses", ["ENROLLED"])),
                            ev.get("startsAt", now),
                            now,
                        ),
                    )
        finally:
            conn.close()

    def _row_to_event(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = 'CONFIRMED';",
            (row["event_id"],),
        ).fetchone()[0]
        capacity = int(row["capacity"])
        return {
            "eventId": row["event_id"],
            "title": row["title"],
            "description": row["description"],
            "capacity": capacity,
            "confirmedCount": confirmed,
            "remainingSlots": max(0, capacity - confirmed),
            "eligibleStatuses": json.loads(row["eligible_statuses_json"]),
            "startsAt": row["starts_at"],
            "createdAt": row["created_at"],
        }

    def list_events(self) -> list[dict[str, Any]]:
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute("SELECT * FROM events ORDER BY event_id ASC;").fetchall()
            return [self._row_to_event(conn, row) for row in rows]
        finally:
            conn.close()

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        conn = open_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?;", (event_id,)).fetchone()
            return self._row_to_event(conn, row) if row else None
        finally:
            conn.close()

    def create_event(
        self,
        *,
        event_id: str | None = None,
        title: str,
        description: str = "",
        capacity: int,
        eligible_statuses: list[str] | None = None,
        starts_at: str | None = None,
    ) -> dict[str, Any]:
        eid = (event_id or f"EVT-{uuid.uuid4().hex[:6].upper()}").strip()
        now = now_rfc3339(self.tz_offset_hours)
        statuses = eligible_statuses if eligible_statuses else ["ENROLLED"]
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                existing = conn.execute(
                    "SELECT 1 FROM events WHERE event_id = ?;", (eid,)
                ).fetchone()
                if existing:
                    raise ServiceError(409, "EVENT_EXISTS", f"Event '{eid}' already exists.")
                conn.execute(
                    """
                    INSERT INTO events (
                        event_id, title, description, capacity,
                        eligible_statuses_json, starts_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        eid,
                        title.strip(),
                        description.strip(),
                        capacity,
                        json.dumps(statuses),
                        starts_at or now,
                        now,
                    ),
                )
            row = conn.execute("SELECT * FROM events WHERE event_id = ?;", (eid,)).fetchone()
            return self._row_to_event(conn, row)
        finally:
            conn.close()

    def update_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        capacity: int | None = None,
        eligible_statuses: list[str] | None = None,
        starts_at: str | None = None,
    ) -> dict[str, Any]:
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                row = conn.execute(
                    "SELECT * FROM events WHERE event_id = ?;", (event_id,)
                ).fetchone()
                if not row:
                    raise ServiceError(404, "EVENT_NOT_FOUND", f"Event '{event_id}' not found.")
                confirmed = conn.execute(
                    "SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = 'CONFIRMED';",
                    (event_id,),
                ).fetchone()[0]
                new_capacity = capacity if capacity is not None else int(row["capacity"])
                if new_capacity < confirmed:
                    raise ServiceError(
                        422,
                        "INVALID_PAYLOAD",
                        f"Capacity ({new_capacity}) cannot be lower than confirmed registrations ({confirmed}).",
                    )
                conn.execute(
                    """
                    UPDATE events
                    SET title = ?, description = ?, capacity = ?,
                        eligible_statuses_json = ?, starts_at = ?
                    WHERE event_id = ?;
                    """,
                    (
                        title.strip() if title is not None else row["title"],
                        description.strip() if description is not None else row["description"],
                        new_capacity,
                        json.dumps(eligible_statuses)
                        if eligible_statuses is not None
                        else row["eligible_statuses_json"],
                        starts_at if starts_at is not None else row["starts_at"],
                        event_id,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM events WHERE event_id = ?;", (event_id,)
            ).fetchone()
            return self._row_to_event(conn, updated)
        finally:
            conn.close()

    def create_registration(
        self,
        *,
        event_id: str,
        student_id: str,
        recipient_email: str,
        enrollment_status: str,
        registration_id_override: str | None = None,
        qr_token_override: str | None = None,
    ) -> dict[str, Any]:
        """Atomically enforce eligibility, duplicate guard, and capacity in BEGIN IMMEDIATE."""
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                event_row = conn.execute(
                    "SELECT capacity, eligible_statuses_json FROM events WHERE event_id = ?;",
                    (event_id,),
                ).fetchone()
                if not event_row:
                    raise ServiceError(404, "EVENT_NOT_FOUND", f"Event '{event_id}' not found.")

                allowed_statuses = set(json.loads(event_row["eligible_statuses_json"]))
                if enrollment_status not in allowed_statuses:
                    raise ServiceError(
                        422,
                        "INELIGIBLE",
                        f"Student enrollment status '{enrollment_status}' is not eligible for event '{event_id}'.",
                    )

                dup = conn.execute(
                    "SELECT registration_id FROM registrations WHERE event_id = ? AND student_id = ?;",
                    (event_id, student_id),
                ).fetchone()
                if dup:
                    raise ServiceError(
                        409,
                        "ALREADY_REGISTERED",
                        f"Student '{student_id}' is already registered for event '{event_id}'.",
                    )

                confirmed_count = conn.execute(
                    "SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = 'CONFIRMED';",
                    (event_id,),
                ).fetchone()[0]
                if confirmed_count >= int(event_row["capacity"]):
                    raise ServiceError(
                        409,
                        "EVENT_FULL",
                        f"Event '{event_id}' has reached its maximum capacity.",
                    )

                reg_id = registration_id_override or f"REG-{uuid.uuid4().hex[:8].upper()}"
                qr_token = qr_token_override or generate_qr_token()
                verifier = compute_token_verifier(qr_token, self.token_hmac_key)
                enc_token = encrypt_token(qr_token, self.token_fernet_key)
                created_at = now_rfc3339(self.tz_offset_hours)

                try:
                    conn.execute(
                        """
                        INSERT INTO registrations (
                            registration_id, event_id, student_id, recipient_email,
                            status, token_verifier, encrypted_token, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            reg_id,
                            event_id,
                            student_id,
                            recipient_email,
                            "CONFIRMED",
                            verifier,
                            enc_token,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ServiceError(
                        409,
                        "ALREADY_REGISTERED",
                        "Duplicate registration constraint triggered.",
                    ) from exc

            return {
                "registrationId": reg_id,
                "eventId": event_id,
                "status": "CONFIRMED",
                "qrToken": qr_token,
            }
        finally:
            conn.close()

    def validate_token(self, event_id: str, qr_token: str) -> dict[str, Any]:
        """Validate a presented QR token against event_id and CONFIRMED status."""
        verifier = compute_token_verifier(qr_token, self.token_hmac_key)
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT registration_id, event_id, status, recipient_email
                FROM registrations
                WHERE token_verifier = ?;
                """,
                (verifier,),
            ).fetchone()
            if not row or row["event_id"] != event_id or row["status"] != "CONFIRMED":
                return {
                    "registrationId": None,
                    "eventId": event_id,
                    "status": None,
                    "valid": False,
                    "recipient": None,
                }
            return {
                "registrationId": row["registration_id"],
                "eventId": row["event_id"],
                "status": row["status"],
                "valid": True,
                "recipient": row["recipient_email"],
            }
        finally:
            conn.close()

    def list_confirmed_registrations(
        self, event_id: str, student_id: str | None = None
    ) -> list[dict[str, str]]:
        """List confirmed registrations for an event without exposing QR tokens."""
        conn = open_connection(self.db_path)
        try:
            event_exists = conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?;", (event_id,)
            ).fetchone()
            if not event_exists:
                raise ServiceError(404, "EVENT_NOT_FOUND", f"Event '{event_id}' not found.")

            if student_id:
                rows = conn.execute(
                    """
                    SELECT registration_id, student_id, status
                    FROM registrations
                    WHERE event_id = ? AND status = 'CONFIRMED' AND student_id = ?
                    ORDER BY created_at ASC;
                    """,
                    (event_id, student_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT registration_id, student_id, status
                    FROM registrations
                    WHERE event_id = ? AND status = 'CONFIRMED'
                    ORDER BY created_at ASC;
                    """,
                    (event_id,),
                ).fetchall()

            return [
                {
                    "registrationId": r["registration_id"],
                    "studentId": r["student_id"],
                    "status": r["status"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def list_student_registrations(self, student_id: str) -> list[dict[str, str]]:
        """Return a student's own confirmed registrations with decrypted QR token."""
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT registration_id, event_id, status, encrypted_token
                FROM registrations
                WHERE student_id = ?
                ORDER BY created_at DESC;
                """,
                (student_id,),
            ).fetchall()
            return [
                {
                    "registrationId": r["registration_id"],
                    "eventId": r["event_id"],
                    "status": r["status"],
                    "qrToken": decrypt_token(r["encrypted_token"], self.token_fernet_key),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def record_failed_notification(
        self,
        *,
        notification_type: str,
        event_id: str,
        registration_id: str,
        correlation_id: str,
        error_message: str,
    ) -> str:
        """Record notification retry exhaustion for authorized review."""
        record_id = f"FAILNOTIF-{uuid.uuid4().hex[:10].upper()}"
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO failed_notifications (
                    id, notification_type, event_id, registration_id,
                    correlation_id, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record_id,
                    notification_type,
                    event_id,
                    registration_id,
                    correlation_id,
                    error_message,
                    now_rfc3339(self.tz_offset_hours),
                ),
            )
            return record_id
        finally:
            conn.close()

    def list_failed_notifications(self) -> list[dict[str, str]]:
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM failed_notifications ORDER BY created_at DESC;"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "type": r["notification_type"],
                    "eventId": r["event_id"],
                    "registrationId": r["registration_id"],
                    "correlationId": r["correlation_id"],
                    "errorMessage": r["error_message"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()
