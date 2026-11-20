from __future__ import annotations

import asyncio

import httpx
import pytest

from clients.scanner_client import ScannerClient
from shared.cluster import ServiceCluster
from shared.schemas import assert_schema
from shared.time_utils import is_valid_rfc3339_with_offset
from shared.tls import build_client_ssl_context


@pytest.mark.e2e
@pytest.mark.timeout(180)
def test_live_https_cluster_full_lifecycle_ui_and_process_kill_fp2(tmp_path):
    cluster = ServiceCluster.build(
        work_dir=tmp_path,
        use_ephemeral_ports=True,
        extra_env={"RETRY_DELAYS_SEC": "0.05,0.05"},
    )
    try:
        cluster.start_all()

        async def run_e2e():
            async with httpx.AsyncClient(
                base_url=cluster.gateway_url,
                verify=build_client_ssl_context(cluster.ca_cert_path),
                timeout=8.0,
                trust_env=False,
            ) as gw:
                # 1. Verify Gateway serves static Web UI assets cleanly over HTTPS
                r_html = await gw.get("/")
                assert r_html.status_code == 200
                assert "FEU Tech CampusPass" in r_html.text
                assert "FEU Institute of Technology" in r_html.text
                assert (await gw.get("/static/styles.css")).status_code == 200
                assert (await gw.get("/static/qr.js")).status_code == 200
                assert (await gw.get("/static/app.js")).status_code == 200
                assert (await gw.get("/static/feu-tech-logo.png")).status_code == 200
                assert (await gw.get("/static/feu-tech-seal.png")).status_code == 200

                async def login(username: str, password: str = "Password123!") -> str:
                    resp = await gw.post(
                        "/api/v1/auth/login",
                        json={"username": username, "password": password},
                    )
                    assert resp.status_code == 200
                    return resp.json()["sessionToken"]

                t_s1 = await login("student_s001")
                t_s2 = await login("student_s002")
                t_s3 = await login("student_s003")
                t_s4 = await login("student_s004")
                t_org = await login("organizer1")
                t_scn = await login("scanner1")
                t_adm = await login("admin1", "AdminPassword123!")

                # 2. Ineligible student S-004 (ON_LEAVE) -> 422 INELIGIBLE
                r_inelig = await gw.post(
                    "/api/v1/events/EVT-001/registrations",
                    json={"studentId": "S-004"},
                    headers={"Authorization": f"Bearer {t_s4}"},
                )
                assert r_inelig.status_code == 422
                assert r_inelig.json()["code"] == "INELIGIBLE"

                # 3. Eligible S-001 registers for EVT-001 -> 201 CONFIRMED
                r1 = await gw.post(
                    "/api/v1/events/EVT-001/registrations",
                    json={"studentId": "S-001"},
                    headers={"Authorization": f"Bearer {t_s1}"},
                )
                assert r1.status_code == 201
                reg1 = r1.json()
                assert_schema("registration_response", reg1)

                # Duplicate S-001 -> 409 ALREADY_REGISTERED
                r1_dup = await gw.post(
                    "/api/v1/events/EVT-001/registrations",
                    json={"studentId": "S-001"},
                    headers={"Authorization": f"Bearer {t_s1}"},
                )
                assert r1_dup.status_code == 409
                assert r1_dup.json()["code"] == "ALREADY_REGISTERED"

                # 4. Eligible S-002 takes 2nd slot -> 201 CONFIRMED
                r2 = await gw.post(
                    "/api/v1/events/EVT-001/registrations",
                    json={"studentId": "S-002"},
                    headers={"Authorization": f"Bearer {t_s2}"},
                )
                assert r2.status_code == 201
                reg2 = r2.json()
                assert_schema("registration_response", reg2)

                # 5. Eligible S-003 hits capacity -> 409 EVENT_FULL
                r3 = await gw.post(
                    "/api/v1/events/EVT-001/registrations",
                    json={"studentId": "S-003"},
                    headers={"Authorization": f"Bearer {t_s3}"},
                )
                assert r3.status_code == 409
                assert r3.json()["code"] == "EVENT_FULL"

                # 6. Scanner checks in S-001 -> 201 CHECKED_IN; repeat -> 409; wrong event -> 404
                scanner = ScannerClient(gw, session_token=t_scn, timeout_sec=8.0)
                chk1 = await scanner.scan_and_check_in("EVT-001", reg1["qrToken"], correlation_id="corr-e2e-chk1")
                assert chk1["state"] == "CHECKED_IN"
                assert is_valid_rfc3339_with_offset(chk1["checkIn"]["checkInTime"]) is True

                chk1_dup = await scanner.scan_and_check_in("EVT-001", reg1["qrToken"], correlation_id="corr-e2e-dup")
                assert chk1_dup["state"] == "ALREADY_CHECKED_IN"

                chk_wrong_evt = await scanner.scan_and_check_in("EVT-002", reg1["qrToken"], correlation_id="corr-e2e-wrong")
                assert chk_wrong_evt["state"] == "INVALID_TOKEN"

                # 7. Real Process Kill Outage (FP 2): kill Attendance Service process!
                cluster.stop_service("attendance-service")
                outage_scan = await scanner.scan_and_check_in(
                    "EVT-001", reg2["qrToken"], correlation_id="corr-e2e-outage"
                )
                assert outage_scan["state"] == "UNVERIFIED"
                assert outage_scan["statusCode"] == 503
                assert outage_scan["registrationId"] == reg2["registrationId"]

                # Restart real Attendance Service process on same port & same DB
                cluster.start_service("attendance-service", wait_ready=True)
                recovered = await scanner.reconcile_and_maybe_retry(
                    "EVT-001",
                    qr_token=reg2["qrToken"],
                    registration_id=outage_scan["registrationId"],
                    correlation_id="corr-e2e-recover",
                )
                assert recovered["state"] == "CHECKED_IN"
                assert recovered["retried"] is True

                # 8. Combined Organizer Report over HTTPS
                rep = await gw.get(
                    "/api/v1/events/EVT-001/reports/attendance",
                    headers={"Authorization": f"Bearer {t_org}"},
                )
                assert rep.status_code == 200
                rep_data = rep.json()
                assert_schema("attendance_report_response", rep_data)
                assert rep_data["registeredCount"] == 2
                assert rep_data["checkedInCount"] == 2
                assert rep_data["notCheckedInCount"] == 0

                # 9. Admin Audit Logs & Health over HTTPS + verify redaction
                health = await gw.get(
                    "/api/v1/admin/service-health",
                    headers={"Authorization": f"Bearer {t_adm}"},
                )
                assert health.status_code == 200
                assert all(v == "ok" for v in health.json()["services"].values())

                audits = await gw.get(
                    "/api/v1/admin/audit-logs",
                    headers={"Authorization": f"Bearer {t_adm}"},
                )
                assert audits.status_code == 200
                audit_json = audits.json()
                assert audit_json["unavailableServices"] == []
                serialized = str(audit_json["logs"])
                assert reg1["qrToken"] not in serialized
                assert reg2["qrToken"] not in serialized
                assert "s001@example.test" not in serialized
                assert "Password123!" not in serialized

        asyncio.run(run_e2e())
    finally:
        cluster.stop_all()
