from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.attendance.app import create_app as create_attendance_app
from services.attendance.repository import SqliteAttendanceRepository
from services.event_registration.app import create_app as create_registration_app
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.schemas import assert_schema
from shared.security import build_service_headers


def _make_settings(tmp_path):
    return load_settings(
        env={
            "STUDENT_DIRECTORY_DB": str(tmp_path / "student_directory.db"),
            "EVENT_REGISTRATION_DB": str(tmp_path / "event_registration.db"),
            "ATTENDANCE_DB": str(tmp_path / "attendance.db"),
            "GATEWAY_SERVICE_SECRET": "gw-secret",
            "EVENT_REGISTRATION_SERVICE_SECRET": "reg-secret",
            "ATTENDANCE_SERVICE_SECRET": "att-secret",
            "TOKEN_HMAC_KEY": "token-hmac-secret",
            "TOKEN_FERNET_KEY": Fernet.generate_key().decode("ascii"),
            "CONTEXT_HMAC_KEY": "ctx-secret",
        }
    )


def _staff_headers(settings, cid: str):
    return build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id=cid,
        user_ctx={
            "accountId": "ACC-SCN1",
            "username": "scanner1",
            "role": "CHECK_IN_STAFF",
            "studentId": None,
            "assignedEventIds": ["EVT-001"],
        },
        context_hmac_key=settings.context_hmac_key,
    )


@pytest.mark.failure_path
def test_fp1_repeated_qr_scan_returns_409_and_preserves_original_record_and_timestamp(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app),
        base_url="https://dir.local",
    )
    reg_app = create_registration_app(
        settings, directory_http_client=dir_http, enable_notifications=False
    )
    reg_app.state.repo.create_registration(
        event_id="EVT-001",
        student_id="S-001",
        recipient_email="s001@example.test",
        enrollment_status="ENROLLED",
        registration_id_override="REG-001",
        qr_token_override="T-001",
    )
    reg_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=reg_app),
        base_url="https://reg.local",
    )

    att_app = create_attendance_app(
        settings, registration_http_client=reg_http, enable_notifications=False
    )
    # Exact FP 1 precondition: REG-001 already checked in with A-001 at 2026-10-01T09:00:00+08:00
    att_app.state.repo.record_check_in(
        event_id="EVT-001",
        registration_id="REG-001",
        attendance_id_override="A-001",
        check_in_time_override="2026-10-01T09:00:00+08:00",
    )
    att_client = TestClient(att_app)

    # Scan T-001 a second time
    resp = att_client.post(
        "/api/v1/events/EVT-001/check-ins",
        json={"qrToken": "T-001"},
        headers=_staff_headers(settings, "corr-fp1-repeat"),
    )
    assert resp.status_code == 409
    err = resp.json()
    assert err["code"] == "ALREADY_CHECKED_IN"
    assert err["correlationId"] == "corr-fp1-repeat"
    assert_schema("error", err)

    # Verify only A-001 exists and its original timestamp is untouched
    records = att_app.state.repo.list_event_attendance("EVT-001")
    assert len(records) == 1
    assert records["REG-001"]["attendanceId"] == "A-001"
    assert records["REG-001"]["checkInTime"] == "2026-10-01T09:00:00+08:00"

    asyncio.run(reg_http.aclose())
    asyncio.run(dir_http.aclose())


@pytest.mark.failure_path
def test_concurrent_qr_scans_record_at_most_one_checkin_per_registration(tmp_path):
    repo = SqliteAttendanceRepository(tmp_path / "att_race.db", tz_offset_hours=8)

    def try_checkin(_idx: int) -> str:
        try:
            res = repo.record_check_in(event_id="EVT-001", registration_id="REG-001")
            return f"OK:{res['attendanceStatus']}"
        except Exception as exc:
            return getattr(exc, "code", str(exc))

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(try_checkin, range(6)))

    assert results.count("OK:CHECKED_IN") == 1
    assert results.count("ALREADY_CHECKED_IN") == 5
    assert len(repo.list_event_attendance("EVT-001")) == 1
