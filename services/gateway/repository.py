"""Gateway repository owning sample accounts, scrypt password hashes, and server sessions."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.schemas import load_fixture
from shared.security import ALL_ROLES, hash_password, verify_password
from shared.sqlite_utils import immediate_transaction, open_connection
from shared.time_utils import format_rfc3339, get_timezone, now_rfc3339, parse_rfc3339


def _hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class SqliteGatewayAuthRepository:
    """Isolated SQLite storage for Gateway accounts, assignments, and server-validated sessions."""

    def __init__(self, db_path: Path | str, *, tz_offset_hours: int = 8) -> None:
        self.db_path = Path(db_path)
        self.tz_offset_hours = tz_offset_hours
        self._init_schema_and_seed()

    def _init_schema_and_seed(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    student_id TEXT,
                    assigned_event_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM accounts;").fetchone()[0]
            if count == 0:
                seed = load_fixture("sample_seed")
                now = now_rfc3339(self.tz_offset_hours)
                for acc in seed["accounts"]:
                    conn.execute(
                        """
                        INSERT INTO accounts (
                            account_id, username, password_hash, role,
                            student_id, assigned_event_ids_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            acc["accountId"],
                            acc["username"],
                            hash_password(acc["password"]),
                            acc["role"],
                            acc.get("studentId"),
                            json.dumps(acc.get("assignedEventIds", [])),
                            now,
                        ),
                    )
        finally:
            conn.close()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "accountId": row["account_id"],
            "username": row["username"],
            "role": row["role"],
            "studentId": row["student_id"],
            "assignedEventIds": json.loads(row["assigned_event_ids_json"]),
        }

    def authenticate_and_create_session(
        self, username: str, password: str, *, ttl_hours: int = 8
    ) -> dict[str, Any] | None:
        """Verify username/password via scrypt and establish a server-validated session."""
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM accounts WHERE username = ?;",
                (username.strip(),),
            ).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                return None

            raw_token = secrets.token_urlsafe(32)
            token_hash = _hash_session_token(raw_token)
            now_dt = datetime.now(get_timezone(self.tz_offset_hours))
            created_at = format_rfc3339(now_dt, self.tz_offset_hours)
            expires_at = format_rfc3339(
                now_dt + timedelta(hours=ttl_hours), self.tz_offset_hours
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    session_token_hash, account_id, expires_at, revoked, created_at
                ) VALUES (?, ?, ?, 0, ?);
                """,
                (token_hash, row["account_id"], expires_at, created_at),
            )
            return {
                "sessionToken": raw_token,
                "expiresAt": expires_at,
                "user": self._row_to_user(row),
            }
        finally:
            conn.close()

    def validate_session(self, raw_token: str) -> dict[str, Any] | None:
        """Validate active, non-expired, non-revoked session token against SQLite."""
        if not raw_token:
            return None
        token_hash = _hash_session_token(raw_token)
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT s.expires_at, s.revoked, a.*
                FROM sessions s
                JOIN accounts a ON s.account_id = a.account_id
                WHERE s.session_token_hash = ?;
                """,
                (token_hash,),
            ).fetchone()
            if not row or int(row["revoked"]) != 0:
                return None
            now_dt = datetime.now(get_timezone(self.tz_offset_hours))
            if parse_rfc3339(row["expires_at"]) <= now_dt:
                return None
            return self._row_to_user(row)
        finally:
            conn.close()

    def revoke_session(self, raw_token: str) -> None:
        if not raw_token:
            return
        token_hash = _hash_session_token(raw_token)
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE session_token_hash = ?;",
                (token_hash,),
            )
        finally:
            conn.close()

    def list_accounts(self) -> list[dict[str, Any]]:
        conn = open_connection(self.db_path)
        try:
            rows = conn.execute("SELECT * FROM accounts ORDER BY account_id ASC;").fetchall()
            return [self._row_to_user(r) for r in rows]
        finally:
            conn.close()

    def create_account(
        self,
        *,
        username: str,
        password: str,
        role: str,
        student_id: str | None = None,
        assigned_event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if role not in ALL_ROLES:
            raise ServiceError(422, "INVALID_PAYLOAD", f"Unsupported role '{role}'.")
        clean_user = username.strip()
        if not clean_user or not password.strip():
            raise ServiceError(422, "INVALID_PAYLOAD", "username and password are required.")
        clean_student_id = student_id.strip() if isinstance(student_id, str) else None
        if role == "STUDENT" and not clean_student_id:
            raise ServiceError(
                422, "INVALID_PAYLOAD", "studentId is required for STUDENT accounts."
            )
        cleaned_events = [e.strip() for e in (assigned_event_ids or [])]
        if any(not e for e in cleaned_events):
            raise ServiceError(422, "INVALID_PAYLOAD", "assignedEventIds entries must not be blank.")
        acc_id = f"ACC-{uuid.uuid4().hex[:6].upper()}"
        now = now_rfc3339(self.tz_offset_hours)
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                exists = conn.execute(
                    "SELECT 1 FROM accounts WHERE username = ?;", (clean_user,)
                ).fetchone()
                if exists:
                    raise ServiceError(
                        409, "ACCOUNT_EXISTS", f"Username '{clean_user}' already exists."
                    )
                conn.execute(
                    """
                    INSERT INTO accounts (
                        account_id, username, password_hash, role,
                        student_id, assigned_event_ids_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        acc_id,
                        clean_user,
                        hash_password(password),
                        role,
                        clean_student_id,
                        json.dumps(cleaned_events),
                        now,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM accounts WHERE account_id = ?;", (acc_id,)
            ).fetchone()
            return self._row_to_user(row)
        finally:
            conn.close()

    def update_assignments(
        self,
        account_id: str,
        *,
        role: str | None = None,
        assigned_event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if role is not None and role not in ALL_ROLES:
            raise ServiceError(422, "INVALID_PAYLOAD", f"Unsupported role '{role}'.")
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                row = conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?;", (account_id,)
                ).fetchone()
                if not row:
                    raise ServiceError(
                        404, "ACCOUNT_NOT_FOUND", f"Account '{account_id}' not found."
                    )
                new_role = role if role is not None else row["role"]
                new_events = (
                    assigned_event_ids
                    if assigned_event_ids is not None
                    else json.loads(row["assigned_event_ids_json"])
                )
                conn.execute(
                    """
                    UPDATE accounts
                    SET role = ?, assigned_event_ids_json = ?
                    WHERE account_id = ?;
                    """,
                    (new_role, json.dumps(new_events), account_id),
                )
            updated = conn.execute(
                "SELECT * FROM accounts WHERE account_id = ?;", (account_id,)
            ).fetchone()
            return self._row_to_user(updated)
        finally:
            conn.close()

    def assign_event_to_account(self, account_id: str, event_id: str) -> None:
        """Auto-assign a newly created event to its organizer account."""
        conn = open_connection(self.db_path)
        try:
            with immediate_transaction(conn):
                row = conn.execute(
                    "SELECT assigned_event_ids_json FROM accounts WHERE account_id = ?;",
                    (account_id,),
                ).fetchone()
                if not row:
                    return
                current = list(json.loads(row["assigned_event_ids_json"]))
                if event_id not in current:
                    current.append(event_id)
                    conn.execute(
                        "UPDATE accounts SET assigned_event_ids_json = ? WHERE account_id = ?;",
                        (json.dumps(current), account_id),
                    )
        finally:
            conn.close()
