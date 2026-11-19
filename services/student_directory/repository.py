"""Student Directory repository and LegacyDirectoryAdapter boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from shared.schemas import load_fixture
from shared.sqlite_utils import open_connection


class LegacyDirectoryAdapter(Protocol):
    """Compatibility boundary for translating future university SIS data into prototype fields."""

    def get_student_eligibility(self, student_id: str, event_id: str) -> dict[str, Any] | None:
        """Return {studentId, eligible, name, email, enrollmentStatus} or None if missing."""
        ...


class SqliteStudentDirectoryRepository:
    """SQLite-backed sample Student Directory implementing LegacyDirectoryAdapter."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_schema_and_seed()

    def _init_schema_and_seed(self) -> None:
        conn = open_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    enrollment_status TEXT NOT NULL,
                    ineligible_event_ids_json TEXT NOT NULL
                );
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM students;").fetchone()[0]
            if count == 0:
                seed = load_fixture("sample_seed")
                for stu in seed["students"]:
                    conn.execute(
                        """
                        INSERT INTO students (
                            student_id, name, email, enrollment_status, ineligible_event_ids_json
                        ) VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            stu["studentId"],
                            stu["name"],
                            stu["email"],
                            stu["enrollmentStatus"],
                            json.dumps(stu.get("ineligibleEventIds", [])),
                        ),
                    )
        finally:
            conn.close()

    def get_student_eligibility(self, student_id: str, event_id: str) -> dict[str, Any] | None:
        """Look up sample student record and compute eligibility for event_id."""
        conn = open_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT student_id, name, email, enrollment_status, ineligible_event_ids_json
                FROM students WHERE student_id = ?;
                """,
                (student_id,),
            ).fetchone()
            if row is None:
                return None

            ineligible_events = set(json.loads(row["ineligible_event_ids_json"]))
            eligible = (
                row["enrollment_status"] == "ENROLLED"
                and event_id not in ineligible_events
            )
            return {
                "studentId": row["student_id"],
                "eligible": eligible,
                "name": row["name"],
                "email": row["email"],
                "enrollmentStatus": row["enrollment_status"],
            }
        finally:
            conn.close()
