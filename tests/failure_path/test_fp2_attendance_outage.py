from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from clients.scanner_client import ScannerClient
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
            "CLIENT_WAIT_TIMEOUT_SEC": "8.0",
        }
    )


def _setup_stack(tmp_path):
    settings = _make_settings(tmp_path)
    dir_app = create_directory_app(settings)
    notif_app = create_notification_app(settings)

    dir_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dir_app), base_url="https://dir.local"
    )
    notif_http_for_reg = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local"
    )
    notif_http_for_att = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local"
    )

    reg_app = create_registration_app(
        settings,
        directory_http_client=dir_http,
        notification_http_client=notif_http_for_reg,
    )
    # Exact FP 2 precondition: REG-002 is confirmed for EVT-001 with token T-002 and has no check-in
    reg_app.state.repo.create_registration(
        event_id="EVT-001",
        student_id="S-002",
        recipient_email="s002@example.test",
        enrollment_status="ENROLLED",
        registration_id_override="REG-002",
        qr_token_override="T-002",
    )

    reg_http_for_att = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local"
    )
    att_app = create_attendance_app(
        settings,
        registration_http_client=reg_http_for_att,
        notification_http_client=notif_http_for_att,
    )
    return settings, dir_app, reg_app, att_app, notif_app, [
        dir_http,
        notif_http_for_reg,
        notif_http_for_att,
        reg_http_for_att,
    ]


@pytest.mark.failure_path
def test_fp2_attendance_outage_reconciles_and_retries_safely_recording_one_checkin(tmp_path):
    settings, dir_app, reg_app, att_app, notif_app, closables = _setup_stack(tmp_path)

    attendance_down = True

    class ToggleableAttendanceTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self._inner = httpx.ASGITransport(app=att_app)

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if attendance_down:
                raise httpx.ConnectError("Attendance Service unreachable", request=request)
            return await self._inner.handle_async_request(request)

    gw_dir = httpx.AsyncClient(transport=httpx.ASGITransport(app=dir_app), base_url="https://dir.local")
    gw_reg = httpx.AsyncClient(transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local")
    gw_att = httpx.AsyncClient(transport=ToggleableAttendanceTransport(), base_url="https://att.local")
    gw_notif = httpx.AsyncClient(transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local")

    gw_app = create_gateway_app(
        settings,
        directory_http_client=gw_dir,
        registration_http_client=gw_reg,
        attendance_http_client=gw_att,
        notification_http_client=gw_notif,
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gw_app), base_url="https://gw.local"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "scanner1", "password": "Password123!"},
            )
            assert login.status_code == 200
            token = login.json()["sessionToken"]

            scanner = ScannerClient(client, session_token=token, timeout_sec=8.0)

            # 1. Submit check-in while Attendance is unreachable -> Gateway returns 503, scanner stays UNVERIFIED
            attempt1 = await scanner.scan_and_check_in("EVT-001", "T-002", correlation_id="corr-fp2-a1")
            assert attempt1["state"] == "UNVERIFIED"
            assert attempt1["statusCode"] == 503
            assert attempt1["registrationId"] == "REG-002"
            assert attempt1["error"]["code"] == "SERVICE_UNAVAILABLE"
            assert_schema("error", attempt1["error"])

            # 2. Restore Attendance Service -> reconcile confirms absence -> safe retry succeeds ONCE
            nonlocal attendance_down
            attendance_down = False

            reconciled = await scanner.reconcile_and_maybe_retry(
                "EVT-001",
                qr_token="T-002",
                registration_id=attempt1["registrationId"],
                correlation_id="corr-fp2-a2",
            )
            assert reconciled["state"] == "CHECKED_IN"
            assert reconciled["retried"] is True
            assert reconciled["checkIn"]["registrationId"] == "REG-002"
            assert_schema("checkin_response", reconciled["checkIn"])

            # Verify strictly 1 attendance record exists in DB
            assert len(att_app.state.repo.list_event_attendance("EVT-001")) == 1

    asyncio.run(scenario())
    for c in [gw_dir, gw_reg, gw_att, gw_notif, *closables]:
        asyncio.run(c.aclose())


@pytest.mark.failure_path
def test_fp2_lost_response_after_commit_discovers_existing_record_and_persistent_outage_stays_unverified(tmp_path):
    settings, dir_app, reg_app, att_app, notif_app, closables = _setup_stack(tmp_path)

    drop_post_response_after_commit = True
    still_unavailable = False

    class DropAfterCommitTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self._inner = httpx.ASGITransport(app=att_app)
            self.checkin_posts = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if still_unavailable:
                raise httpx.ConnectError("Attendance still down", request=request)
            if request.method == "POST" and request.url.path.endswith("/check-ins"):
                self.checkin_posts += 1
                resp = await self._inner.handle_async_request(request)
                if drop_post_response_after_commit:
                    raise httpx.ReadTimeout("Simulated lost response after commit", request=request)
                return resp
            return await self._inner.handle_async_request(request)

    att_transport = DropAfterCommitTransport()
    gw_dir = httpx.AsyncClient(transport=httpx.ASGITransport(app=dir_app), base_url="https://dir.local")
    gw_reg = httpx.AsyncClient(transport=httpx.ASGITransport(app=reg_app), base_url="https://reg.local")
    gw_att = httpx.AsyncClient(transport=att_transport, base_url="https://att.local")
    gw_notif = httpx.AsyncClient(transport=httpx.ASGITransport(app=notif_app), base_url="https://notif.local")

    gw_app = create_gateway_app(
        settings,
        directory_http_client=gw_dir,
        registration_http_client=gw_reg,
        attendance_http_client=gw_att,
        notification_http_client=gw_notif,
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gw_app), base_url="https://gw.local"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "scanner1", "password": "Password123!"},
            )
            token = login.json()["sessionToken"]
            scanner = ScannerClient(client, session_token=token, timeout_sec=8.0)

            # Sub-scenario (b): first request commits on server, response lost -> UNVERIFIED
            attempt = await scanner.scan_and_check_in("EVT-001", "T-002", correlation_id="corr-fp2-b1")
            assert attempt["state"] == "UNVERIFIED"
            assert attempt["registrationId"] == "REG-002"
            assert att_transport.checkin_posts == 1

            # Sub-scenario (c): if status query also cannot reach Attendance, outcome stays UNVERIFIED
            nonlocal still_unavailable
            still_unavailable = True
            stuck = await scanner.reconcile_and_maybe_retry(
                "EVT-001",
                qr_token="T-002",
                registration_id="REG-002",
                correlation_id="corr-fp2-c1",
            )
            assert stuck["state"] == "UNVERIFIED"
            assert stuck["retried"] is False
            assert att_transport.checkin_posts == 1

            # Restore status endpoint -> reconciliation discovers existing record WITHOUT calling POST /check-ins again!
            still_unavailable = False
            discovered = await scanner.reconcile_and_maybe_retry(
                "EVT-001",
                qr_token="T-002",
                registration_id="REG-002",
                correlation_id="corr-fp2-b2",
            )
            assert discovered["state"] == "CHECKED_IN"
            assert discovered["retried"] is False
            assert discovered["checkIn"]["registrationId"] == "REG-002"
            assert att_transport.checkin_posts == 1  # Never retried after discovering committed record
            assert len(att_app.state.repo.list_event_attendance("EVT-001")) == 1

    asyncio.run(scenario())
    for c in [gw_dir, gw_reg, gw_att, gw_notif, *closables]:
        asyncio.run(c.aclose())
