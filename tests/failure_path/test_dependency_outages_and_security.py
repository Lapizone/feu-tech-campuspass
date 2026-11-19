from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.attendance.app import create_app as create_attendance_app
from services.event_registration.app import create_app as create_registration_app
from services.gateway.app import create_app as create_gateway_app
from services.notification.app import create_app as create_notification_app
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.schemas import assert_schema


def _make_settings(tmp_path):
    return load_settings(
        env={
            "STUDENT_DIRECTORY_DB": str(tmp_path / "student_directory.db"),
            "EVENT_REGISTRATION_DB": str(tmp_path / "event_registration.db"),
            "ATTENDANCE_DB": str(tmp_path / "attendance.db"),
            "NOTIFICATION_DB": str(tmp_path / "notification.db"),
            "GATEWAY_AUTH_DB": str(tmp_path / "gateway_auth.db"),
            "GATEWAY_SERVICE_SECRET": "gw-secret",
            "EVENT_REGISTRATION_SERVICE_SECRET": "reg-secret",
            "ATTENDANCE_SERVICE_SECRET": "att-secret",
            "TOKEN_HMAC_KEY": "token-hmac-secret",
            "TOKEN_FERNET_KEY": Fernet.generate_key().decode("ascii"),
            "CONTEXT_HMAC_KEY": "ctx-secret",
            "SESSION_SECRET": "sess-secret",
            "RETRY_DELAYS_SEC": "0.01,0.01",
        }
    )


def _login(client: TestClient, username: str, password: str = "Password123!") -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    token = resp.json()["sessionToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.failure_path
def test_rbac_matrix_cross_event_isolation_and_internal_endpoint_protection(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    notif_app = create_notification_app(settings)

    dir_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=dir_app), base_url="https://dir.local")
    notif_reg = httpx.AsyncClient(transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local")
    notif_att = httpx.AsyncClient(transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local")

    reg_app = create_registration_app(
        settings, directory_http_client=dir_http, notification_http_client=notif_reg
    )
    reg_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local")
    att_app = create_attendance_app(
        settings, registration_http_client=reg_http, notification_http_client=notif_att
    )

    gw_dir = httpx.AsyncClient(transport=httpx.ASGITransport(app=dir_app), base_url="https://dir.local")
    gw_reg = httpx.AsyncClient(transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local")
    gw_att = httpx.AsyncClient(transport=httpx.ASGITransport(app=att_app), base_url="https://att.local")
    gw_notif = httpx.AsyncClient(transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local")

    gw_app = create_gateway_app(
        settings,
        directory_http_client=gw_dir,
        registration_http_client=gw_reg,
        attendance_http_client=gw_att,
        notification_http_client=gw_notif,
    )
    client = TestClient(gw_app)

    # 1. Unauthenticated requests -> 401 UNAUTHORIZED
    r_401 = client.get("/api/v1/events")
    assert r_401.status_code == 401
    assert r_401.json()["code"] == "UNAUTHORIZED"
    assert_schema("error", r_401.json())

    # Invalid password -> 401 UNAUTHORIZED
    r_bad_pw = client.post("/api/v1/auth/login", json={"username": "student_s001", "password": "WrongPassword!"})
    assert r_bad_pw.status_code == 401
    assert r_bad_pw.json()["code"] == "UNAUTHORIZED"

    s1_hdr = _login(client, "student_s001")
    org1_hdr = _login(client, "organizer1")   # assigned EVT-001, EVT-002
    org2_hdr = _login(client, "organizer2")   # assigned EVT-002 ONLY
    scn1_hdr = _login(client, "scanner1")     # assigned EVT-001, EVT-002
    scn2_hdr = _login(client, "scanner2")     # assigned EVT-002 ONLY
    adm_hdr = _login(client, "admin1", "AdminPassword123!")

    # 2. Student registers own S-001 for EVT-001 -> 201, spoofing S-002 -> 403
    r_reg = client.post("/api/v1/events/EVT-001/registrations", json={"studentId": "S-001"}, headers=s1_hdr)
    assert r_reg.status_code == 201
    qr_token = r_reg.json()["qrToken"]
    reg_id = r_reg.json()["registrationId"]

    assert client.post("/api/v1/events/EVT-002/registrations", json={"studentId": "S-002"}, headers=s1_hdr).status_code == 403
    # Empty studentId for authenticated student must validate as 422 INVALID_PAYLOAD, not 403
    r_empty_sid = client.post("/api/v1/events/EVT-002/registrations", json={"studentId": ""}, headers=s1_hdr)
    assert r_empty_sid.status_code == 422
    assert r_empty_sid.json()["code"] == "INVALID_PAYLOAD"

    # Unsafe eventId characters (e.g. quotes) are rejected as 422 INVALID_PAYLOAD
    r_unsafe_eid = client.post(
        "/api/v1/events",
        json={"eventId": 'EVT"BAD', "title": "Bad Event", "capacity": 5},
        headers=org1_hdr,
    )
    assert r_unsafe_eid.status_code == 422
    assert r_unsafe_eid.json()["code"] == "INVALID_PAYLOAD"

    # Gateway /registrations/validate for CHECK_IN_STAFF omits recipient PII
    r_val_staff = client.post(
        "/api/v1/registrations/validate",
        json={"eventId": "EVT-001", "qrToken": qr_token},
        headers=scn1_hdr,
    )
    assert r_val_staff.status_code == 200
    assert r_val_staff.json()["valid"] is True
    assert "recipient" not in r_val_staff.json()

    # Student cannot access internal/admin/organizer/scanner endpoints -> 403
    assert client.post("/api/v1/events", json={"title": "Rogue", "capacity": 5}, headers=s1_hdr).status_code == 403
    assert client.post("/api/v1/registrations/validate", json={"eventId": "EVT-001", "qrToken": qr_token}, headers=s1_hdr).status_code == 403
    assert client.post("/api/v1/events/EVT-001/check-ins", json={"qrToken": qr_token}, headers=s1_hdr).status_code == 403
    assert client.get("/api/v1/events/EVT-001/reports/attendance", headers=s1_hdr).status_code == 403
    assert client.get("/api/v1/students/S-001/eligibility?eventId=EVT-001", headers=s1_hdr).status_code == 403
    assert client.get("/api/v1/admin/audit-logs", headers=s1_hdr).status_code == 403

    # Ordinary student token hitting backend Registration internal validate endpoint directly -> 401 UNAUTHORIZED
    direct_reg = TestClient(reg_app)
    assert direct_reg.post(
        "/api/v1/registrations/validate",
        json={"eventId": "EVT-001", "qrToken": qr_token},
        headers=s1_hdr,
    ).status_code == 401

    # 3. Cross-event isolation: organizer2 & scanner2 (EVT-002 only) get 403 on EVT-001
    assert client.get("/api/v1/events/EVT-001/reports/attendance", headers=org2_hdr).status_code == 403
    assert client.put("/api/v1/events/EVT-001", json={"capacity": 5}, headers=org2_hdr).status_code == 403
    assert client.post("/api/v1/events/EVT-001/check-ins", json={"qrToken": qr_token}, headers=scn2_hdr).status_code == 403
    assert client.get(f"/api/v1/events/EVT-001/registrations/{reg_id}/attendance", headers=scn2_hdr).status_code == 403

    # Assigned organizer1 & scanner1 succeed on EVT-001
    assert client.post("/api/v1/events/EVT-001/check-ins", json={"qrToken": qr_token}, headers=scn1_hdr).status_code == 201
    rep = client.get("/api/v1/events/EVT-001/reports/attendance", headers=org1_hdr)
    assert rep.status_code == 200
    assert rep.json()["checkedInCount"] == 1

    # Organizer2 creates a new event -> auto-assigned so organizer2 can view its report
    new_ev = client.post(
        "/api/v1/events",
        json={"eventId": "EVT-NEW", "title": "Org2 Seminar", "capacity": 10},
        headers=org2_hdr,
    )
    assert new_ev.status_code == 201
    assert client.get("/api/v1/events/EVT-NEW/reports/attendance", headers=org2_hdr).status_code == 200

    # 4. Boundary 415/400/422 enforcement via Gateway
    assert client.post("/api/v1/events/EVT-001/check-ins", content=b'{"qrToken":"x"}', headers={**scn1_hdr, "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/v1/events/EVT-001/check-ins", content=b"{bad", headers={**scn1_hdr, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/v1/events/EVT-001/check-ins", json={"qrToken": ""}, headers=scn1_hdr).status_code == 422

    # 5. Admin reads aggregated audit logs across all 5 services & verifies redaction
    audit_resp = client.get("/api/v1/admin/audit-logs", headers=adm_hdr)
    assert audit_resp.status_code == 200
    audit_body = audit_resp.json()
    assert audit_body["unavailableServices"] == []
    assert len(audit_body["logs"]) >= 5
    serialized_logs = str(audit_body["logs"])
    assert qr_token not in serialized_logs
    assert "Password123!" not in serialized_logs
    assert "s001@example.test" not in serialized_logs

    # Logout revokes session -> subsequent request gets 401
    assert client.post("/api/v1/auth/logout", headers=s1_hdr).status_code == 200
    assert client.get("/api/v1/my/registrations", headers=s1_hdr).status_code == 401

    for c in [dir_http, notif_reg, notif_att, reg_http, gw_dir, gw_reg, gw_att, gw_notif]:
        asyncio.run(c.aclose())


@pytest.mark.failure_path
def test_directory_outage_blocks_registration_with_503_and_creates_no_record(tmp_path):
    settings = _make_settings(tmp_path)

    class DownDirectory(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Student Directory offline", request=request)

    dir_down = httpx.AsyncClient(transport=DownDirectory(), base_url="https://dir.local")
    reg_app = create_registration_app(
        settings, directory_http_client=dir_down, enable_notifications=False
    )
    gw_reg = httpx.AsyncClient(transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local")
    gw_app = create_gateway_app(
        settings,
        directory_http_client=dir_down,
        registration_http_client=gw_reg,
        attendance_http_client=gw_reg,
        notification_http_client=gw_reg,
    )
    client = TestClient(gw_app)
    s1_hdr = _login(client, "student_s001")

    r_reg = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-001"},
        headers=s1_hdr,
    )
    assert r_reg.status_code == 503
    assert r_reg.json()["code"] == "SERVICE_UNAVAILABLE"
    assert reg_app.state.repo.list_confirmed_registrations("EVT-001") == []

    asyncio.run(gw_reg.aclose())
    asyncio.run(dir_down.aclose())
