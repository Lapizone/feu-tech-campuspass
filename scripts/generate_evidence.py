"""Generate Proposal Section 10 evaluation evidence artifacts from real runs."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from clients.scanner_client import ScannerClient
from shared.audit import redact_payload
from shared.cluster import ServiceCluster
from shared.time_utils import now_rfc3339
from shared.tls import build_client_ssl_context


def _run_pytest_capture() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--tb=short"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def _redact_trace(obj: Any, extra_tokens: list[str] | None = None) -> Any:
    sanitized = redact_payload(obj)
    if not extra_tokens:
        return sanitized
    raw = json.dumps(sanitized)
    for tok in extra_tokens:
        if tok:
            raw = raw.replace(tok, "[REDACTED_QR_TOKEN]")
    return json.loads(raw)


async def _capture_live_traces(work_dir: Path) -> dict[str, Any]:
    cluster = ServiceCluster.build(
        work_dir=work_dir,
        use_ephemeral_ports=True,
        extra_env={"RETRY_DELAYS_SEC": "0.05,0.05"},
    )
    traces: list[dict[str, Any]] = []
    tokens_to_scrub: list[str] = []

    try:
        cluster.start_all()
        async with httpx.AsyncClient(
            base_url=cluster.gateway_url,
            verify=build_client_ssl_context(cluster.ca_cert_path),
            timeout=8.0,
        ) as gw:

            async def call(
                label: str,
                method: str,
                path: str,
                *,
                token: str | None = None,
                payload: dict[str, Any] | None = None,
                corr_id: str,
            ) -> httpx.Response:
                headers = {"X-Correlation-Id": corr_id}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                resp = await gw.request(method, path, json=payload, headers=headers)
                resp_body = resp.json()
                if isinstance(resp_body, dict) and isinstance(resp_body.get("qrToken"), str):
                    tokens_to_scrub.append(resp_body["qrToken"])
                traces.append(
                    {
                        "label": label,
                        "method": method,
                        "path": path,
                        "correlationId": corr_id,
                        "requestBody": payload,
                        "statusCode": resp.status_code,
                        "responseBody": resp_body,
                    }
                )
                return resp

            async def login(username: str, password: str, corr_id: str) -> str:
                r = await call(
                    f"Authenticate {username}",
                    "POST",
                    "/api/v1/auth/login",
                    payload={"username": username, "password": password},
                    corr_id=corr_id,
                )
                assert r.status_code == 200
                return r.json()["sessionToken"]

            s1_tok = await login("student_s001", "Password123!", "corr-ev-login-s1")
            s2_tok = await login("student_s002", "Password123!", "corr-ev-login-s2")
            s3_tok = await login("student_s003", "Password123!", "corr-ev-login-s3")
            s4_tok = await login("student_s004", "Password123!", "corr-ev-login-s4")
            scn_tok = await login("scanner1", "Password123!", "corr-ev-login-scn")
            org_tok = await login("organizer1", "Password123!", "corr-ev-login-org")
            adm_tok = await login("admin1", "AdminPassword123!", "corr-ev-login-adm")

            # Ineligible path (S-004 ON_LEAVE)
            await call(
                "Ineligible student registration (422 INELIGIBLE)",
                "POST",
                "/api/v1/events/EVT-001/registrations",
                token=s4_tok,
                payload={"studentId": "S-004"},
                corr_id="corr-ev-ineligible",
            )

            # Eligible S-001 registers -> 201 CONFIRMED
            r_s1 = await call(
                "Eligible S-001 registration (201 CONFIRMED)",
                "POST",
                "/api/v1/events/EVT-001/registrations",
                token=s1_tok,
                payload={"studentId": "S-001"},
                corr_id="corr-ev-reg-s1",
            )
            qr1 = r_s1.json()["qrToken"]
            reg1_id = r_s1.json()["registrationId"]

            # Duplicate registration S-001 -> 409 ALREADY_REGISTERED
            await call(
                "Duplicate S-001 registration (409 ALREADY_REGISTERED)",
                "POST",
                "/api/v1/events/EVT-001/registrations",
                token=s1_tok,
                payload={"studentId": "S-001"},
                corr_id="corr-ev-reg-dup",
            )

            # Eligible S-002 registers (takes 2nd capacity slot; intentionally stays not checked in for comparison)
            r_s2 = await call(
                "Eligible S-002 registration (201 CONFIRMED)",
                "POST",
                "/api/v1/events/EVT-001/registrations",
                token=s2_tok,
                payload={"studentId": "S-002"},
                corr_id="corr-ev-reg-s2",
            )
            qr2 = r_s2.json()["qrToken"]

            # Full capacity S-003 -> 409 EVENT_FULL
            await call(
                "Full event capacity S-003 (409 EVENT_FULL)",
                "POST",
                "/api/v1/events/EVT-001/registrations",
                token=s3_tok,
                payload={"studentId": "S-003"},
                corr_id="corr-ev-reg-full",
            )

            # Valid check-in S-001 -> 201 CHECKED_IN
            await call(
                "Validate token for EVT-001",
                "POST",
                "/api/v1/registrations/validate",
                token=scn_tok,
                payload={"eventId": "EVT-001", "qrToken": qr1},
                corr_id="corr-ev-val-s1",
            )
            await call(
                "Check in S-001 (201 CHECKED_IN)",
                "POST",
                "/api/v1/events/EVT-001/check-ins",
                token=scn_tok,
                payload={"qrToken": qr1},
                corr_id="corr-ev-chk-s1",
            )

            # Repeated check-in S-001 -> 409 ALREADY_CHECKED_IN
            await call(
                "Repeat scan S-001 (409 ALREADY_CHECKED_IN)",
                "POST",
                "/api/v1/events/EVT-001/check-ins",
                token=scn_tok,
                payload={"qrToken": qr1},
                corr_id="corr-ev-chk-dup",
            )

            # Wrong-event check-in S-001 at EVT-002 -> 404 INVALID_TOKEN
            await call(
                "Wrong-event scan S-001 at EVT-002 (404 INVALID_TOKEN)",
                "POST",
                "/api/v1/events/EVT-002/check-ins",
                token=scn_tok,
                payload={"qrToken": qr1},
                corr_id="corr-ev-chk-wrong",
            )

            # Service outage demonstration for S-002 (FP 2) then recovery leaving S-002 unchecked or recovered
            # Here we demonstrate outage -> 503 UNVERIFIED -> restart -> reconcile absence -> keep S-002 unchecked or check EVT-002!
            cluster.stop_service("attendance-service")
            scanner = ScannerClient(gw, session_token=scn_tok, timeout_sec=8.0)
            outage = await scanner.scan_and_check_in(
                "EVT-001", qr2, correlation_id="corr-ev-outage"
            )
            traces.append(
                {
                    "label": "Attendance outage during S-002 check-in (503 UNVERIFIED)",
                    "method": "POST",
                    "path": "/api/v1/events/EVT-001/check-ins",
                    "correlationId": "corr-ev-outage",
                    "requestBody": {"qrToken": qr2},
                    "statusCode": outage["statusCode"],
                    "responseBody": outage["error"],
                }
            )
            cluster.start_service("attendance-service", wait_ready=True)

            # Query confirmed registrations & combined report (S-001 checked in, S-002 not checked in)
            reg_list_resp = await call(
                "Authoritative confirmed registrations for EVT-001",
                "GET",
                "/api/v1/events/EVT-001/registrations?status=CONFIRMED",
                token=org_tok,
                corr_id="corr-ev-reg-list",
            )
            s1_att_resp = await call(
                "Authoritative attendance status for REG-S1",
                "GET",
                f"/api/v1/events/EVT-001/registrations/{reg1_id}/attendance",
                token=scn_tok,
                corr_id="corr-ev-att-s1",
            )
            report_resp = await call(
                "Combined attendance report for EVT-001",
                "GET",
                "/api/v1/events/EVT-001/reports/attendance",
                token=org_tok,
                corr_id="corr-ev-report",
            )
            audit_resp = await gw.get(
                "/api/v1/admin/audit-logs?limit=25",
                headers={
                    "Authorization": f"Bearer {adm_tok}",
                    "X-Correlation-Id": "corr-ev-audit",
                },
            )

            redacted_traces = [_redact_trace(t, tokens_to_scrub) for t in traces]
            redacted_audits = _redact_trace(audit_resp.json(), tokens_to_scrub)
            return {
                "traces": redacted_traces,
                "registrations": reg_list_resp.json(),
                "sampleAttendance": s1_att_resp.json(),
                "report": report_resp.json(),
                "audits": redacted_audits,
            }
    finally:
        cluster.stop_all()


def main() -> None:
    evidence_dir = ROOT_DIR / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    generated_at = now_rfc3339()

    rc, pytest_output = _run_pytest_capture()
    if rc != 0:
        raise SystemExit(f"pytest failed with exit code {rc}:\n{pytest_output}")

    with tempfile.TemporaryDirectory() as tmp:
        captured = asyncio.run(_capture_live_traces(Path(tmp)))

    # 1. evidence/test-results.md
    test_results_md = f"""# Section 10 Evidence: Verification & Test Results

Generated: `{generated_at}`

## Proposal Six Integration-Focused Test Cases Mapping

| Case | Category | Clause | Automated Test File | Status |
| :--- | :--- | :--- | :--- | :--- |
| **CT 1** | Contract Test | C1 | [test_ct1_eligibility_contract.py](../tests/contract/test_ct1_eligibility_contract.py) | PASSED |
| **CT 2** | Contract Test | C2 | [test_ct2_registration_contract.py](../tests/contract/test_ct2_registration_contract.py) | PASSED |
| **IT 1** | Interface Test | C3 | [test_it1_valid_checkin.py](../tests/interface/test_it1_valid_checkin.py) | PASSED |
| **IT 2** | Interface Test | C4 | [test_it2_registration_notification.py](../tests/interface/test_it2_registration_notification.py) | PASSED |
| **FP 1** | Failure-Path Test | C5 | [test_fp1_repeated_qr_scan.py](../tests/failure_path/test_fp1_repeated_qr_scan.py) | PASSED |
| **FP 2** | Failure-Path Test | C6 | [test_fp2_attendance_outage.py](../tests/failure_path/test_fp2_attendance_outage.py) & [test_live_https_stack.py](../tests/e2e/test_live_https_stack.py) | PASSED |

## Full Pytest Execution Output (`python -m pytest -v`)

```text
{pytest_output}
```
"""
    (evidence_dir / "test-results.md").write_text(test_results_md, encoding="utf-8")

    # 2. evidence/sample-request-response-logs.md
    trace_blocks: list[str] = []
    for idx, t in enumerate(captured["traces"], start=1):
        req_str = json.dumps(t["requestBody"], indent=2) if t["requestBody"] is not None else "null"
        resp_str = json.dumps(t["responseBody"], indent=2)
        trace_blocks.append(
            f"### {idx}. {t['label']}\n\n"
            f"- **Request**: `{t['method']} {t['path']}` (Correlation ID: `{t['correlationId']}`)\n"
            f"- **HTTP Status**: `{t['statusCode']}`\n\n"
            f"```json\n// Request Body\n{req_str}\n\n// Response Body\n{resp_str}\n```\n"
        )

    audit_preview = json.dumps(captured["audits"]["logs"][:12], indent=2)
    logs_md = f"""# Section 10 Evidence: Sample Request/Response & Redacted Audit Logs

Generated: `{generated_at}`

All HTTP requests and responses below were captured over HTTPS (`127.0.0.1`) against the running five-service cluster with TLS verification enabled. Raw `qrToken` values, passwords, session credentials, and sensitive PII fields are redacted (`[REDACTED]` / `[REDACTED_QR_TOKEN]`).

{''.join(trace_blocks)}
## Aggregated Redacted Audit Logs (`GET /api/v1/admin/audit-logs`)

Unavailable services during capture: `{captured['audits']['unavailableServices']}`

```json
{audit_preview}
```
"""
    (evidence_dir / "sample-request-response-logs.md").write_text(logs_md, encoding="utf-8")

    # 3. evidence/registration-vs-attendance-comparison.md
    regs = captured["registrations"]["registrations"]
    report = captured["report"]
    rows = []
    for p in report["participants"]:
        rows.append(
            f"| `{p['registrationId']}` | `{p['studentId']}` | `{p['registrationStatus']}` | "
            f"`{'CHECKED_IN' if p['checkedIn'] else 'NOT_CHECKED_IN'}` | `{p['checkInTime'] or 'null'}` |"
        )
    comparison_md = f"""# Section 10 Evidence: Registration vs Attendance Record Comparison

Generated: `{generated_at}`

This comparison demonstrates Proposal Section 4 ("Revised: Traceability of integration objectives to project objectives"): Event Registration and Attendance Service maintain separate SQLite databases and reconcile records through documented `/api/v1/` interfaces matched on `(eventId, registrationId)` without manual spreadsheet matching.

## Event Summary (`{report['eventId']}`)

- **Confirmed Registrations (`Event Registration`)**: `{len(regs)}`
- **Report `registeredCount`**: `{report['registeredCount']}`
- **Report `checkedInCount`**: `{report['checkedInCount']}`
- **Report `notCheckedInCount`**: `{report['notCheckedInCount']}`

## Matched Participant Records (`eventId={report['eventId']}`)

| Registration ID | Student ID | Registration Status | Attendance Status | Check-in Time (RFC 3339) |
| :--- | :--- | :--- | :--- | :--- |
{chr(10).join(rows)}

## Raw Interface Payloads

### Event Registration (`GET /api/v1/events/EVT-001/registrations?status=CONFIRMED`)

```json
{json.dumps(captured['registrations'], indent=2)}
```

### Attendance Service Combined Report (`GET /api/v1/events/EVT-001/reports/attendance`)

```json
{json.dumps(report, indent=2)}
```
"""
    (evidence_dir / "registration-vs-attendance-comparison.md").write_text(
        comparison_md, encoding="utf-8"
    )
    print("Generated Section 10 evidence artifacts in evidence/.")


if __name__ == "__main__":
    main()
