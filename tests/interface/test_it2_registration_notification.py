from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.attendance.app import create_app as create_attendance_app
from services.event_registration.app import create_app as create_registration_app
from services.notification.app import create_app as create_notification_app
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.schemas import assert_schema, load_fixture
from shared.security import build_service_headers


def _make_settings(tmp_path):
    return load_settings(
        env={
            "STUDENT_DIRECTORY_DB": str(tmp_path / "student_directory.db"),
            "EVENT_REGISTRATION_DB": str(tmp_path / "event_registration.db"),
            "ATTENDANCE_DB": str(tmp_path / "attendance.db"),
            "NOTIFICATION_DB": str(tmp_path / "notification.db"),
            "GATEWAY_SERVICE_SECRET": "gw-secret",
            "EVENT_REGISTRATION_SERVICE_SECRET": "reg-secret",
            "ATTENDANCE_SERVICE_SECRET": "att-secret",
            "TOKEN_HMAC_KEY": "token-hmac-secret",
            "TOKEN_FERNET_KEY": Fernet.generate_key().decode("ascii"),
            "CONTEXT_HMAC_KEY": "ctx-secret",
            "RETRY_DELAYS_SEC": "0.01,0.01",
            "NOTIFICATION_TIMEOUT_SEC": "3.0",
        }
    )


@pytest.mark.interface
def test_it2_notification_accepts_request_is_idempotent_and_redacts_logs(tmp_path):
    settings = _make_settings(tmp_path)
    notif_app = create_notification_app(settings)
    client = TestClient(notif_app)

    it2 = load_fixture("it2_notification_accepted")
    headers = build_service_headers(
        service_id="event-registration",
        service_secret=settings.event_registration_service_secret,
        correlation_id="corr-it2-01",
    )

    resp = client.post("/api/v1/notifications", json=it2["request"], headers=headers)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "ACCEPTED"
    assert isinstance(body["notificationId"], str) and len(body["notificationId"]) > 0
    assert_schema("notification_response", body)

    # Idempotency on (type, eventId, registrationId): repeat returns same notificationId
    resp_dup = client.post("/api/v1/notifications", json=it2["request"], headers=headers)
    assert resp_dup.status_code == 202
    assert resp_dup.json()["notificationId"] == body["notificationId"]
    assert resp_dup.json()["status"] == "ACCEPTED"

    # Unsupported notification type -> 422 INVALID_PAYLOAD
    bad_req = {**it2["request"], "type": "UNKNOWN_TYPE"}
    r_422 = client.post("/api/v1/notifications", json=bad_req, headers=headers)
    assert r_422.status_code == 422
    assert r_422.json()["code"] == "INVALID_PAYLOAD"

    # Also verify raw QR token and plaintext email are stripped from audit logs and DB
    secret_req = {
        "type": "QR_ISSUED",
        "recipient": "s002@example.test",
        "eventId": "EVT-001",
        "registrationId": "REG-002",
        "deliveryDetails": {"qrToken": "QR-SECRET-VALUE-999"},
    }
    assert client.post("/api/v1/notifications", json=secret_req, headers=headers).status_code == 202

    logs = notif_app.state.audit.list_logs()
    serialized = str(logs)
    assert "QR-SECRET-VALUE-999" not in serialized
    assert "qrToken" not in serialized
    assert "s001@example.test" not in serialized
    assert "s002@example.test" not in serialized
    assert b"QR-SECRET-VALUE-999" not in (tmp_path / "notification.db").read_bytes()


@pytest.mark.interface
def test_notification_outage_never_reverses_committed_registration_or_checkin(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app),
        base_url="https://dir.local",
    )

    notif_calls = 0

    def failing_notif_handler(request: httpx.Request) -> httpx.Response:
        nonlocal notif_calls
        notif_calls += 1
        return httpx.Response(
            503,
            json={
                "code": "SERVICE_UNAVAILABLE",
                "message": "Notification queue unavailable.",
                "correlationId": "corr-notif-down",
            },
        )

    failing_notif_http = httpx.AsyncClient(
        transport=httpx.MockTransport(failing_notif_handler),
        base_url="https://notif.local",
    )

    reg_app = create_registration_app(
        settings,
        directory_http_client=dir_http,
        notification_http_client=failing_notif_http,
    )
    reg_client = TestClient(reg_app)

    student_headers = build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id="corr-reg-notif-down",
        user_ctx={
            "accountId": "ACC-S001",
            "username": "student_s001",
            "role": "STUDENT",
            "studentId": "S-001",
            "assignedEventIds": [],
        },
        context_hmac_key=settings.context_hmac_key,
    )

    # Registration MUST still return 201 CONFIRMED despite Notification 503 after 2 retries (3 total calls)
    reg_resp = reg_client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-001"},
        headers=student_headers,
    )
    assert reg_resp.status_code == 201
    reg_body = reg_resp.json()
    assert reg_body["status"] == "CONFIRMED"
    assert notif_calls == 3  # 1 initial + 2 retries

    # Failed notification recorded for authorized review
    failed_reg_notifs = reg_app.state.repo.list_failed_notifications()
    assert len(failed_reg_notifs) == 1
    assert failed_reg_notifs[0]["registrationId"] == reg_body["registrationId"]

    # Check-in MUST also succeed (201 CHECKED_IN) when Notification is unavailable
    reg_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=reg_app),
        base_url="https://reg.local",
    )
    att_app = create_attendance_app(
        settings,
        registration_http_client=reg_http,
        notification_http_client=failing_notif_http,
    )
    att_client = TestClient(att_app)
    staff_headers = build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id="corr-chk-notif-down",
        user_ctx={
            "accountId": "ACC-SCN1",
            "username": "scanner1",
            "role": "CHECK_IN_STAFF",
            "studentId": None,
            "assignedEventIds": ["EVT-001"],
        },
        context_hmac_key=settings.context_hmac_key,
    )
    chk_resp = att_client.post(
        "/api/v1/events/EVT-001/check-ins",
        json={"qrToken": reg_body["qrToken"]},
        headers=staff_headers,
    )
    assert chk_resp.status_code == 201
    assert chk_resp.json()["attendanceStatus"] == "CHECKED_IN"
    assert len(att_app.state.repo.list_failed_notifications()) == 1

    asyncio.run(reg_http.aclose())
    asyncio.run(failing_notif_http.aclose())
    asyncio.run(dir_http.aclose())
