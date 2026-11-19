from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.event_registration.app import create_app as create_registration_app
from services.event_registration.repository import SqliteEventRegistrationRepository
from services.student_directory.app import create_app as create_directory_app
from shared.config import load_settings
from shared.security import build_service_headers, compute_token_verifier


def _make_settings(tmp_path):
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
        }
    )


def _student_headers(settings, student_id: str, cid: str):
    return build_service_headers(
        service_id="gateway",
        service_secret=settings.gateway_service_secret,
        correlation_id=cid,
        user_ctx={
            "accountId": f"ACC-{student_id}",
            "username": f"student_{student_id.lower()}",
            "role": "STUDENT",
            "studentId": student_id,
            "assignedEventIds": [],
        },
        context_hmac_key=settings.context_hmac_key,
    )


@pytest.mark.unit
def test_duplicate_registration_capacity_limit_and_ineligibility_rules(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app),
        base_url="https://dir.local",
    )
    reg_app = create_registration_app(settings, directory_http_client=dir_http)
    client = TestClient(reg_app)

    # S-004 (ON_LEAVE) and S-005 (ineligible for EVT-001) get 422 INELIGIBLE
    r_leave = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-004"},
        headers=_student_headers(settings, "S-004", "corr-s004"),
    )
    assert r_leave.status_code == 422
    assert r_leave.json()["code"] == "INELIGIBLE"

    r_inelig = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-005"},
        headers=_student_headers(settings, "S-005", "corr-s005"),
    )
    assert r_inelig.status_code == 422
    assert r_inelig.json()["code"] == "INELIGIBLE"

    # S-001 registers -> 201
    r1 = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-001"},
        headers=_student_headers(settings, "S-001", "corr-s001-1"),
    )
    assert r1.status_code == 201

    # Duplicate S-001 -> 409 ALREADY_REGISTERED
    r1_dup = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-001"},
        headers=_student_headers(settings, "S-001", "corr-s001-2"),
    )
    assert r1_dup.status_code == 409
    assert r1_dup.json()["code"] == "ALREADY_REGISTERED"

    # S-002 takes 2nd and final slot of EVT-001 (capacity=2) -> 201
    r2 = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-002"},
        headers=_student_headers(settings, "S-002", "corr-s002"),
    )
    assert r2.status_code == 201
    assert r2.json()["qrToken"] != r1.json()["qrToken"]

    # S-003 (eligible!) tries to register when EVT-001 is full -> 409 EVENT_FULL
    r3 = client.post(
        "/api/v1/events/EVT-001/registrations",
        json={"studentId": "S-003"},
        headers=_student_headers(settings, "S-003", "corr-s003"),
    )
    assert r3.status_code == 409
    assert r3.json()["code"] == "EVENT_FULL"

    # Student spoofing another studentId -> 403 FORBIDDEN
    r_spoof = client.post(
        "/api/v1/events/EVT-002/registrations",
        json={"studentId": "S-002"},
        headers=_student_headers(settings, "S-001", "corr-spoof"),
    )
    assert r_spoof.status_code == 403
    assert r_spoof.json()["code"] == "FORBIDDEN"

    asyncio.run(dir_http.aclose())


@pytest.mark.unit
def test_concurrent_last_slot_and_duplicate_registration_protected_by_immediate_tx(tmp_path):
    settings = _make_settings(tmp_path)
    repo = SqliteEventRegistrationRepository(
        settings.event_registration_db,
        token_hmac_key=settings.token_hmac_key,
        token_fernet_key=settings.token_fernet_key,
        tz_offset_hours=settings.tz_offset_hours,
    )
    repo.create_event(
        event_id="EVT-RACE",
        title="One-Slot Race Event",
        description="Concurrency guard test",
        capacity=1,
        eligible_statuses=["ENROLLED"],
        starts_at="2026-10-20T09:00:00+08:00",
    )

    # 5 concurrent attempts across S-001 and S-002 for capacity=1
    def attempt(student_id: str) -> str:
        try:
            reg = repo.create_registration(
                event_id="EVT-RACE",
                student_id=student_id,
                recipient_email=f"{student_id.lower()}@example.test",
                enrollment_status="ENROLLED",
            )
            return f"OK:{reg['registrationId']}"
        except Exception as exc:
            return getattr(exc, "code", str(exc))

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(attempt, ["S-001", "S-001", "S-002", "S-003", "S-002"]))

    ok_results = [o for o in outcomes if o.startswith("OK:")]
    assert len(ok_results) == 1
    regs = repo.list_confirmed_registrations("EVT-RACE")
    assert len(regs) == 1


@pytest.mark.unit
def test_stored_registration_never_persists_raw_qr_token_in_plaintext(tmp_path):
    settings = _make_settings(tmp_path)
    repo = SqliteEventRegistrationRepository(
        settings.event_registration_db,
        token_hmac_key=settings.token_hmac_key,
        token_fernet_key=settings.token_fernet_key,
    )
    created = repo.create_registration(
        event_id="EVT-001",
        student_id="S-001",
        recipient_email="s001@example.test",
        enrollment_status="ENROLLED",
    )
    raw_token = created["qrToken"]
    raw_db_bytes = tmp_path.joinpath("event_registration.db").read_bytes()
    assert raw_token.encode("utf-8") not in raw_db_bytes

    expected_verifier = compute_token_verifier(raw_token, settings.token_hmac_key)
    validation = repo.validate_token("EVT-001", raw_token)
    assert validation["valid"] is True
    assert validation["registrationId"] == created["registrationId"]
    assert validation["status"] == "CONFIRMED"
    assert expected_verifier != raw_token
