from __future__ import annotations

import asyncio

import httpx
import jsonschema
import pytest
from fastapi.testclient import TestClient

from clients.registration_client import RegistrationClient
from services.event_registration.app import create_app as create_registration_app
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.errors import ServiceError
from shared.schemas import assert_schema, load_fixture
from shared.security import build_service_headers


def _make_settings(tmp_path):
    from cryptography.fernet import Fernet

    return load_settings(
        env={
            "STUDENT_DIRECTORY_DB": str(tmp_path / "student_directory.db"),
            "EVENT_REGISTRATION_DB": str(tmp_path / "event_registration.db"),
            "GATEWAY_SERVICE_SECRET": "gw-secret",
            "EVENT_REGISTRATION_SERVICE_SECRET": "reg-secret",
            "ATTENDANCE_SERVICE_SECRET": "att-secret",
            "TOKEN_HMAC_KEY": "token-hmac-secret",
            "TOKEN_FERNET_KEY": Fernet.generate_key().decode("ascii"),
            "CONTEXT_HMAC_KEY": "ctx-secret",
            "INTERNAL_ELIGIBILITY_TIMEOUT_SEC": "2.0",
            "REGISTRATION_DEADLINE_SEC": "5.0",
            "NOTIFICATION_TIMEOUT_SEC": "3.0",
        }
    )


@pytest.mark.contract
def test_ct2_confirmed_registration_producer_and_consumer_share_schema(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_transport = httpx.ASGITransport(app=dir_app)
    dir_http = httpx.AsyncClient(transport=dir_transport, base_url="https://dir.local")

    reg_app = create_registration_app(
        settings,
        directory_http_client=dir_http,
        notification_http_client=None,
        enable_notifications=False,
    )
    reg_client = TestClient(reg_app)

    student_ctx = {
        "accountId": "ACC-S001",
        "username": "student_s001",
        "role": "STUDENT",
        "studentId": "S-001",
        "assignedEventIds": [],
    }
    headers = build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id="corr-ct2-01",
        user_ctx=student_ctx,
        context_hmac_key=settings.context_hmac_key,
    )

    resp = reg_client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-001"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["eventId"] == "EVT-001"
    assert body["status"] == "CONFIRMED"
    assert isinstance(body["registrationId"], str) and len(body["registrationId"]) > 0
    assert isinstance(body["qrToken"], str) and len(body["qrToken"]) >= 32
    assert "S-001" not in body["qrToken"]
    assert_schema("registration_response", body)

    # Confirm exactly one registration exists in authoritative listing
    org_headers = build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id="corr-ct2-list",
        user_ctx={
            "accountId": "ACC-ORG1",
            "username": "organizer1",
            "role": "EVENT_ORGANIZER",
            "studentId": None,
            "assignedEventIds": ["EVT-001"],
        },
        context_hmac_key=settings.context_hmac_key,
    )
    list_resp = reg_client.get(
        "/api/v1/events/EVT-001/registrations?status=CONFIRMED",
        headers=org_headers,
    )
    assert list_resp.status_code == 200
    assert_schema("registrations_list_response", list_resp.json())
    assert len(list_resp.json()["registrations"]) == 1
    assert list_resp.json()["registrations"][0]["studentId"] == "S-001"

    # Consumer validates shared fixture and rejects malformed registration response
    fixture_payload = load_fixture("ct2_registration_confirmed")
    assert_schema("registration_response", fixture_payload)

    async def verify_consumer():
        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/events/EVT-001/registrations":
                return httpx.Response(201, json=fixture_payload)
            return httpx.Response(201, json={"registrationId": "REG-X", "eventId": "EVT-BAD", "status": "CONFIRMED"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(mock_handler),
            base_url="https://gw.local",
        ) as gw_http:
            sdk = RegistrationClient(gw_http)
            parsed = await sdk.register("EVT-001", "S-001", headers={})
            assert parsed == fixture_payload

            with pytest.raises(ServiceError) as exc_info:
                await sdk.register("EVT-BAD", "S-001", headers={})
            assert exc_info.value.status_code == 503

    asyncio.run(verify_consumer())
    asyncio.run(dir_http.aclose())
