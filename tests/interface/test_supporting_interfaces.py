from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.attendance.app import create_app as create_attendance_app
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
            "RETRY_DELAYS_SEC": "0.01,0.01",
        }
    )


def _org_headers(settings, cid: str):
    return build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id=cid,
        user_ctx={
            "accountId": "ACC-ORG1",
            "username": "organizer1",
            "role": "EVENT_ORGANIZER",
            "studentId": None,
            "assignedEventIds": ["EVT-001", "EVT-002"],
        },
        context_hmac_key=settings.context_hmac_key,
    )


@pytest.mark.interface
def test_combined_attendance_report_matches_registrations_and_status_queries(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app),
        base_url="https://dir.local",
    )
    reg_app = create_registration_app(
        settings, directory_http_client=dir_http, enable_notifications=False
    )
    # Two confirmed registrations for EVT-001: REG-001 (S-001) and REG-002 (S-002)
    reg_app.state.repo.create_registration(
        event_id="EVT-001",
        student_id="S-001",
        recipient_email="s001@example.test",
        enrollment_status="ENROLLED",
        registration_id_override="REG-001",
        qr_token_override="QR-TOKEN-ALPHA",
    )
    reg_app.state.repo.create_registration(
        event_id="EVT-001",
        student_id="S-002",
        recipient_email="s002@example.test",
        enrollment_status="ENROLLED",
        registration_id_override="REG-002",
        qr_token_override="QR-TOKEN-BETA",
    )

    reg_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=reg_app),
        base_url="https://reg.local",
    )
    att_app = create_attendance_app(
        settings, registration_http_client=reg_http, enable_notifications=False
    )
    # Check in only REG-001; REG-002 remains not checked in
    att_app.state.repo.record_check_in(
        event_id="EVT-001",
        registration_id="REG-001",
        attendance_id_override="A-001",
        check_in_time_override="2026-10-01T09:00:00+08:00",
    )

    reg_client = TestClient(reg_app)
    att_client = TestClient(att_app)

    # 1. GET /events/EVT-001/registrations?status=CONFIRMED must never expose qrToken
    list_resp = reg_client.get(
        "/api/v1/events/EVT-001/registrations?status=CONFIRMED",
        headers=_org_headers(settings, "corr-sup-list"),
    )
    assert list_resp.status_code == 200
    assert_schema("registrations_list_response", list_resp.json())
    assert "qrToken" not in list_resp.text
    assert "QR-TOKEN-ALPHA" not in list_resp.text and "QR-TOKEN-BETA" not in list_resp.text

    # 2. GET attendance status for unchecked REG-002 returns checkedIn=False, checkInTime=None
    absent_resp = att_client.get(
        "/api/v1/events/EVT-001/registrations/REG-002/attendance",
        headers=_org_headers(settings, "corr-sup-absent"),
    )
    assert absent_resp.status_code == 200
    assert absent_resp.json() == {
        "eventId": "EVT-001",
        "registrationId": "REG-002",
        "checkedIn": False,
        "checkInTime": None,
    }
    assert_schema("attendance_status_response", absent_resp.json())

    # 3. GET /events/EVT-001/reports/attendance returns matched counts and participants
    rep_resp = att_client.get(
        "/api/v1/events/EVT-001/reports/attendance",
        headers=_org_headers(settings, "corr-sup-report"),
    )
    assert rep_resp.status_code == 200
    report = rep_resp.json()
    assert_schema("attendance_report_response", report)
    assert report["registeredCount"] == 2
    assert report["checkedInCount"] == 1
    assert report["notCheckedInCount"] == 1
    by_reg = {p["registrationId"]: p for p in report["participants"]}
    assert by_reg["REG-001"]["checkedIn"] is True
    assert by_reg["REG-001"]["checkInTime"] == "2026-10-01T09:00:00+08:00"
    assert by_reg["REG-002"]["checkedIn"] is False
    assert by_reg["REG-002"]["checkInTime"] is None

    asyncio.run(reg_http.aclose())
    asyncio.run(dir_http.aclose())


@pytest.mark.interface
def test_report_returns_503_instead_of_partial_totals_when_registration_is_unavailable(tmp_path):
    settings = _make_settings(tmp_path)

    def down_reg_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": "SERVICE_UNAVAILABLE",
                "message": "Registration down",
                "correlationId": "corr-down",
            },
        )

    down_reg_http = httpx.AsyncClient(
        transport=httpx.MockTransport(down_reg_handler),
        base_url="https://reg.local",
    )
    att_app = create_attendance_app(
        settings, registration_http_client=down_reg_http, enable_notifications=False
    )
    att_app.state.repo.record_check_in(event_id="EVT-001", registration_id="REG-001")

    att_client = TestClient(att_app)
    resp = att_client.get(
        "/api/v1/events/EVT-001/reports/attendance",
        headers=_org_headers(settings, "corr-rep-503"),
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "SERVICE_UNAVAILABLE"
    assert_schema("error", resp.json())

    asyncio.run(down_reg_http.aclose())
