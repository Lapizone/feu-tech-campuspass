from __future__ import annotations

import asyncio

import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient

from services.event_registration.directory_client import DirectoryClient
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.errors import ServiceError
from shared.schemas import assert_schema, load_fixture
from shared.security import build_service_headers


def _make_settings(tmp_path):
    return load_settings(
        env={
            "STUDENT_DIRECTORY_DB": str(tmp_path / "student_directory.db"),
            "GATEWAY_SERVICE_SECRET": "gw-secret",
            "EVENT_REGISTRATION_SERVICE_SECRET": "reg-secret",
            "ATTENDANCE_SERVICE_SECRET": "att-secret",
            "CONTEXT_HMAC_KEY": "ctx-secret",
            "INTERNAL_ELIGIBILITY_TIMEOUT_SEC": "2.0",
            "REGISTRATION_DEADLINE_SEC": "5.0",
        }
    )


@pytest.mark.contract
def test_ct1_producer_and_consumer_share_eligibility_fixture_and_reject_missing_eligible(tmp_path):
    settings = _make_settings(tmp_path)
    app = create_directory_app(settings)
    client = TestClient(app)

    headers = build_service_headers(
        service_id="event-registration",
        service_secret=settings.event_registration_service_secret,
        correlation_id="corr-ct1-01",
    )
    resp = client.get("/api/v1/students/S-001/eligibility?eventId=EVT-001", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    expected_fixture = load_fixture("ct1_eligibility_valid")
    assert body == expected_fixture
    assert_schema("eligibility_response", body)

    # Registration consumer accepts the valid shared fixture and rejects missing 'eligible'
    async def check_consumer():
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/students/S-001/eligibility":
                if request.url.params.get("eventId") == "EVT-BAD":
                    return httpx.Response(200, json=load_fixture("ct1_eligibility_missing_field"))
                return httpx.Response(200, json=expected_fixture)
            return httpx.Response(404, json={"code": "STUDENT_NOT_FOUND", "message": "missing", "correlationId": "c"})

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://dir.local") as ac:
            dc = DirectoryClient(ac, settings)
            ok = await dc.check_eligibility("S-001", "EVT-001", "corr-ct1-consumer")
            assert ok == expected_fixture

            with pytest.raises(ServiceError) as exc_info:
                await dc.check_eligibility("S-001", "EVT-BAD", "corr-ct1-bad")
            assert exc_info.value.status_code == 503
            assert exc_info.value.code == "SERVICE_UNAVAILABLE"

    asyncio.run(check_consumer())

    # Double-check negative fixture fails schema directly too
    with pytest.raises(jsonschema.ValidationError):
        assert_schema("eligibility_response", load_fixture("ct1_eligibility_missing_field"))


@pytest.mark.contract
def test_directory_handles_ineligible_missing_student_and_validation_errors(tmp_path):
    settings = _make_settings(tmp_path)
    app = create_directory_app(settings)
    client = TestClient(app)
    headers = build_service_headers(
        service_id="event-registration",
        service_secret=settings.event_registration_service_secret,
        correlation_id="corr-ct1-02",
    )

    # S-004 is ON_LEAVE -> eligible=False
    r_leave = client.get("/api/v1/students/S-004/eligibility?eventId=EVT-001", headers=headers)
    assert r_leave.status_code == 200
    assert r_leave.json()["eligible"] is False
    assert r_leave.json()["enrollmentStatus"] == "ON_LEAVE"
    assert_schema("eligibility_response", r_leave.json())

    # S-005 is ENROLLED but explicitly ineligible for EVT-001, eligible for EVT-002
    r_e1 = client.get("/api/v1/students/S-005/eligibility?eventId=EVT-001", headers=headers)
    assert r_e1.status_code == 200
    assert r_e1.json()["eligible"] is False

    r_e2 = client.get("/api/v1/students/S-005/eligibility?eventId=EVT-002", headers=headers)
    assert r_e2.status_code == 200
    assert r_e2.json()["eligible"] is True

    # Unknown student -> 404 STUDENT_NOT_FOUND
    r_404 = client.get("/api/v1/students/S-999/eligibility?eventId=EVT-001", headers=headers)
    assert r_404.status_code == 404
    assert r_404.json()["code"] == "STUDENT_NOT_FOUND"
    assert_schema("error", r_404.json())

    # Missing or blank eventId -> 422 INVALID_PAYLOAD
    r_422 = client.get("/api/v1/students/S-001/eligibility?eventId=", headers=headers)
    assert r_422.status_code == 422
    assert r_422.json()["code"] == "INVALID_PAYLOAD"
    assert_schema("error", r_422.json())


@pytest.mark.contract
def test_directory_enforces_service_credentials_and_rejects_student_sessions(tmp_path):
    settings = _make_settings(tmp_path)
    app = create_directory_app(settings)
    client = TestClient(app)

    # Missing credentials -> 401
    r_401 = client.get("/api/v1/students/S-001/eligibility?eventId=EVT-001")
    assert r_401.status_code == 401
    assert r_401.json()["code"] == "UNAUTHORIZED"

    # Unauthorized caller service (attendance-service has no business calling Directory) -> 403
    att_headers = build_service_headers(
        service_id="attendance-service",
        service_secret=settings.attendance_service_secret,
        correlation_id="corr-att-unauth",
    )
    r_403_svc = client.get("/api/v1/students/S-001/eligibility?eventId=EVT-001", headers=att_headers)
    assert r_403_svc.status_code == 403
    assert r_403_svc.json()["code"] == "FORBIDDEN"

    # Ordinary student context forwarded via gateway -> 403 FORBIDDEN
    student_headers = build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id="corr-gw-student",
        user_ctx={
            "accountId": "ACC-S001",
            "username": "student_s001",
            "role": "STUDENT",
            "studentId": "S-001",
            "assignedEventIds": [],
        },
        context_hmac_key=settings.context_hmac_key,
    )
    r_403_role = client.get("/api/v1/students/S-001/eligibility?eventId=EVT-001", headers=student_headers)
    assert r_403_role.status_code == 403
    assert r_403_role.json()["code"] == "FORBIDDEN"
