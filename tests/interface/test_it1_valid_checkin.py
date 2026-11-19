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
from shared.schemas import assert_schema, load_fixture
from shared.security import build_service_headers
from shared.time_utils import is_valid_rfc3339_with_offset


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
            "INTERNAL_VALIDATE_TIMEOUT_SEC": "2.0",
            "CHECKIN_DEADLINE_SEC": "5.0",
            "REPORTING_DEADLINE_SEC": "10.0",
        }
    )


def _staff_headers(settings, event_ids: list[str], cid: str):
    return build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id=cid,
        user_ctx={
            "accountId": "ACC-SCN1",
            "username": "scanner1",
            "role": "CHECK_IN_STAFF",
            "studentId": None,
            "assignedEventIds": event_ids,
        },
        context_hmac_key=settings.context_hmac_key,
    )


@pytest.mark.interface
def test_it1_valid_checkin_creates_exactly_one_record_and_matches_schema(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app),
        base_url="https://dir.local",
    )
    reg_app = create_registration_app(
        settings, directory_http_client=dir_http, enable_notifications=False
    )

    # Precondition from IT 1: EVT-001 has confirmed REG-001 with token T-001 and no attendance record
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
    att_client = TestClient(att_app)

    resp = att_client.post(
        "/api/v1/events/EVT-001/check-ins",
        json={"qrToken": "T-001"},
        headers=_staff_headers(settings, ["EVT-001", "EVT-002"], "corr-it1-01"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["registrationId"] == "REG-001"
    assert body["attendanceStatus"] == "CHECKED_IN"
    assert is_valid_rfc3339_with_offset(body["checkInTime"]) is True
    assert_schema("checkin_response", body)

    # Shared fixture also aligns with schema
    assert_schema("checkin_response", load_fixture("it1_checkin_valid"))

    # Reading attendance status returns exactly one check-in for REG-001
    status_resp = att_client.get(
        "/api/v1/events/EVT-001/registrations/REG-001/attendance",
        headers=_staff_headers(settings, ["EVT-001"], "corr-it1-status"),
    )
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body == {
        "eventId": "EVT-001",
        "registrationId": "REG-001",
        "checkedIn": True,
        "checkInTime": body["checkInTime"],
    }
    assert_schema("attendance_status_response", status_body)

    asyncio.run(reg_http.aclose())
    asyncio.run(dir_http.aclose())


@pytest.mark.interface
def test_checkin_rejects_wrong_event_or_invalid_token_and_malformed_validation_response(tmp_path):
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
    att_client = TestClient(att_app)

    # Wrong-event token: T-001 belongs to EVT-001, scanned at EVT-002 -> 404 INVALID_TOKEN
    r_wrong = att_client.post(
        "/api/v1/events/EVT-002/check-ins",
        json={"qrToken": "T-001"},
        headers=_staff_headers(settings, ["EVT-001", "EVT-002"], "corr-wrong-evt"),
    )
    assert r_wrong.status_code == 404
    assert r_wrong.json()["code"] == "INVALID_TOKEN"

    # Nonexistent token -> 404 INVALID_TOKEN
    r_bogus = att_client.post(
        "/api/v1/events/EVT-001/check-ins",
        json={"qrToken": "T-UNKNOWN-999"},
        headers=_staff_headers(settings, ["EVT-001"], "corr-bogus"),
    )
    assert r_bogus.status_code == 404
    assert r_bogus.json()["code"] == "INVALID_TOKEN"

    # Malformed/incomplete validation response from Registration -> 503 SERVICE_UNAVAILABLE, no check-in saved
    def bad_validate_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"registrationId": "REG-001", "eventId": "EVT-001"})  # missing valid & status

    bad_reg_http = httpx.AsyncClient(
        transport=httpx.MockTransport(bad_validate_handler),
        base_url="https://reg.local",
    )
    bad_settings = load_settings(
        env={
            "ATTENDANCE_DB": str(tmp_path / "att_bad.db"),
            "GATEWAY_SERVICE_SECRET": settings.gateway_service_secret,
            "EVENT_REGISTRATION_SERVICE_SECRET": settings.event_registration_service_secret,
            "ATTENDANCE_SERVICE_SECRET": settings.attendance_service_secret,
            "CONTEXT_HMAC_KEY": settings.context_hmac_key,
        }
    )
    att_bad = create_attendance_app(
        bad_settings,
        registration_http_client=bad_reg_http,
        enable_notifications=False,
    )
    r_bad = TestClient(att_bad).post(
        "/api/v1/events/EVT-001/check-ins",
        json={"qrToken": "T-001"},
        headers=_staff_headers(settings, ["EVT-001"], "corr-bad-dep"),
    )
    assert r_bad.status_code == 503
    assert r_bad.json()["code"] == "SERVICE_UNAVAILABLE"

    asyncio.run(bad_reg_http.aclose())
    asyncio.run(reg_http.aclose())
    asyncio.run(dir_http.aclose())
