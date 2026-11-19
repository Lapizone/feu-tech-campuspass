"""Notification repository with idempotency on (type, event_id, registration_id)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.audit import redact_payload
from shared.sqlite_utils import immediate_transaction, open_connection
from shared.time_utils import now_rfc3339


def _mask_recipient(recipient: str) -> str:
    """Mask email/recipient identifier so raw personal emails are minimized at rest."""
    clean = recipient.strip()
    if "@" not in clean:
        return "[REDACTED_RECIPIENT]"
    local, domain = clean.split("@", 1)
    prefix = local[:1] if local else "*"
    return f"{prefix}***@{domain}"


class SqliteNotificationRepository:
    """Isolated SQLite storage for Notification Service."""

    def __init__(self, db_path: Path | str, *, tz_offset_hours: int = 8) -> None:
        self.db_path = Path(db_path)
        self.tz_offset_hours = tz_offset_hours
        self._init_schema()

    def _init_schema(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    recipient_masked TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    registration_id TEXT NOT NULL,
                    delivery_details_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(type, event_id, registration_id)
                );
                """
            )
        finally:
            conn.close()

    def accept_notification(
        self,
        *,
        notification_type: str,
        recipient: str,
        event_id: str,
        registration_id: str,
        delivery_details: dict[str, Any],
    ) -> dict[str, Any]:
        """Idempotently accept notification request by (type, eventId, registrationId)."""
        conn = open_connection(self.db_path)
        safe_details = json.dumps(redact_payload(delivery_details), separators=(",", ":"))
        masked_recipient = _mask_recipient(recipient)
        now = now_rfc3339(self.tz_offset_hours)
        try:
            with immediate_transaction(conn):
                existing = conn.execute(
                    """
                    SELECT notification_id, status
                    FROM notifications
                    WHERE type = ? AND event_id = ? AND registration_id = ?;
                    """,
                    (notification_type, event_id, registration_id),
                ).fetchone()
                if existing:
                    return {
                        "notificationId": existing["notification_id"],
                        "status": existing["status"],
                        "duplicate": True,
                    }

                notif_id = f"NOTIF-{uuid.uuid4().hex[:8].upper()}"
                try:
                    conn.execute(
                        """
                        INSERT INTO notifications (
                            notification_id, type, recipient_masked, event_id,
                            registration_id, delivery_details_json, status,
                            delivery_status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            notif_id,
                            notification_type,
                            masked_recipient,
                            event_id,
                            registration_id,
                            safe_details,
                            "ACCEPTED",
                            "QUEUED",
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    row = conn.execute(
                        """
                        SELECT notification_id, status
                        FROM notifications
                        WHERE type = ? AND event_id = ? AND registration_id = ?;
                        """,
                        (notification_type, event_id, registration_id),
                    ).fetchone()
                    return {
                        "notificationId": row["notification_id"],
                        "status": row["status"],
                        "duplicate": True,
                    }

            return {
                "notificationId": notif_id,
                "status": "ACCEPTED",
                "duplicate": False,
            }
        finally:
            conn.close()

    def complete_simulated_delivery(self, notification_id: str) -> None:
        """Update asynchronous simulated delivery status after HTTP 202 acceptance."""
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE notifications
                SET delivery_status = 'SENT_SIMULATED', updated_at = ?
                WHERE notification_id = ?;
                """,
                (now_rfc3339(self.tz_offset_hours), notification_id),
            )
        finally:
            conn.close()

    def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM notifications WHERE notification_id = ?;",
                (notification_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def list_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?;",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "notificationId": row["notification_id"],
            "type": row["type"],
            "recipientMasked": row["recipient_masked"],
            "eventId": row["event_id"],
            "registrationId": row["registration_id"],
            "status": row["status"],
            "deliveryStatus": row["delivery_status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
