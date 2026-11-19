"""Attendance repository owning check-ins with transactional uniqueness guards."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.sqlite_utils import immediate_transaction, open_connection
from shared.time_utils import now_rfc3339


class SqliteAttendanceRepository:
    """Isolated SQLite storage for Attendance Service."""

    def __init__(self, db_path: Path | str, *, tz_offset_hours: int = 8) -> None:
        self.db_path = Path(db_path)
        self.tz_offset_hours = tz_offset_hours
        self._init_schema()

    def _init_schema(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_records (
                    attendance_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    attendance_status TEXT NOT NULL,
                    check_in_time TEXT NOT NULL,
                    UNIQUE(event_id, registration_id)
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
        finally:
            conn.close()

    def record_check_in(
        self,
        *,
        event_id: str,
        registration_id: str,
        attendance_id_override: str | None = None,
        check_in_time_override: str | None = None,
    ) -> dict[str, str]:
        """Atomically record at most one check-in per (event_id, registration_id)."""
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                existing = conn.execute(
                    """
                    SELECT attendance_id, check_in_time
                    FROM attendance_records
                    WHERE event_id = ? AND registration_id = ?;
                    """,
                    (event_id, registration_id),
                ).fetchone()
                if existing:
                    raise ServiceError(
                        409,
                        "ALREADY_CHECKED_IN",
                        f"Registration '{registration_id}' is already checked in for event '{event_id}'.",
                    )

                att_id = attendance_id_override or f"ATT-{uuid.uuid4().hex[:8].upper()}"
                check_in_time = check_in_time_override or now_rfc3339(self.tz_offset_hours)
                try:
                    conn.execute(
                        """
                        INSERT INTO attendance_records (
                            attendance_id, event_id, registration_id,
                            attendance_status, check_in_time
                        ) VALUES (?, ?, ?, ?, ?);
                        """,
                        (att_id, event_id, registration_id, "CHECKED_IN", check_in_time),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ServiceError(
                        409,
                        "ALREADY_CHECKED_IN",
                        "Duplicate attendance constraint triggered.",
                    ) from exc

            return {
                "registrationId": registration_id,
                "attendanceStatus": "CHECKED_IN",
                "checkInTime": check_in_time,
            }
        finally:
            conn.close()

    def get_attendance_status(
        self, event_id: str, registration_id: str
    ) -> dict[str, Any]:
        """Query authoritative check-in status for reconciliation before retry."""
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT check_in_time
                FROM attendance_records
                WHERE event_id = ? AND registration_id = ?;
                """,
                (event_id, registration_id),
            ).fetchone()
            if not row:
                return {
                    "eventId": event_id,
                    "registrationId": registration_id,
                    "checkedIn": False,
                    "checkInTime": None,
                }
            return {
                "eventId": event_id,
                "registrationId": registration_id,
                "checkedIn": True,
                "checkInTime": row["check_in_time"],
            }
        finally:
            conn.close()

    def list_event_attendance(self, event_id: str) -> dict[str, dict[str, str]]:
        """Return attendance records for an event keyed by registrationId."""
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT attendance_id, event_id, registration_id, attendance_status, check_in_time
                FROM attendance_records
                WHERE event_id = ?
                ORDER BY check_in_time ASC;
                """,
                (event_id,),
            ).fetchall()
            return {
                r["registration_id"]: {
                    "attendanceId": r["attendance_id"],
                    "eventId": r["event_id"],
                    "registrationId": r["registration_id"],
                    "attendanceStatus": r["attendance_status"],
                    "checkInTime": r["check_in_time"],
                }
                for r in rows
            }
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
