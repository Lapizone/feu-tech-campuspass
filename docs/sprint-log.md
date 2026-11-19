# Scrum Working Arrangement & Definition of Done Log

Implements **Section 7 (Scrum working arrangement)** of the written proposal: six fixed two-week sprints (~12 weeks total), with early integration and verifiable increments.

| Sprint | Proposal Focus & Reviewable Increment | Implemented Deliverables | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **1** | Agree on sample data, component boundaries, OpenAPI contracts, event eligibility rules, and test fixtures. | `contracts/openapi.yaml` (3.1.1), 13 JSON schemas (`contracts/schemas/`), 7 fixtures (`contracts/fixtures/`), `shared/` foundation (`config`, `time_utils`, `errors`, `middleware`, `security`, `audit`, `schemas`, `http_client`, `tls`), `scripts/init_runtime.py`. | `tests/unit/` (17 tests passing) |
| **2** | Connect Directory verification to Registration; show eligible and ineligible paths. | `services/student_directory/` (`LegacyDirectoryAdapter`, SQLite seed, `/eligibility`), `services/event_registration/directory_client.py`. | `tests/contract/test_ct1_eligibility_contract.py` (CT 1 / C1) |
| **3** | Implement event creation, transactional capacity and duplicate checks, registration, and token issuance. | `services/event_registration/` (`BEGIN IMMEDIATE`, `UNIQUE(event_id, student_id)`, opaque token + HMAC verifier + Fernet encryption), `clients/registration_client.py`. | `tests/contract/test_ct2_registration_contract.py` (CT 2 / C2), `tests/unit/test_capacity_and_uniqueness.py` |
| **4** | Connect Attendance to registration validation; show valid, wrong-event, and repeated-scan behavior. | `services/attendance/` (`registration_client.py`, `UNIQUE(event_id, registration_id)`, check-in, status query, combined report). | `tests/interface/test_it1_valid_checkin.py` (IT 1 / C3), `tests/failure_path/test_fp1_repeated_qr_scan.py` (FP 1 / C5) |
| **5** | Connect Notifications and failure handling; test unavailable services and ambiguous retries. | `services/notification/` (idempotent `(type, eventId, registrationId)`, simulated async delivery, redaction), `services/gateway/` (`scrypt` auth, RBAC, 8s proxy, aggregated logs/health), `clients/scanner_client.py`. | `tests/interface/test_it2_registration_notification.py` (IT 2 / C4), `tests/interface/test_supporting_interfaces.py`, `tests/failure_path/test_fp2_attendance_outage.py` (FP 2 / C6), `tests/failure_path/test_dependency_outages_and_security.py` |
| **6** | Run all integration-focused cases, fix defects, finalize reports, demonstration, and proposal evidence. | Zero-CDN Web UI (`web/`), `scripts/run_all.py`, live HTTPS multi-process tests (`tests/e2e/test_live_https_stack.py`), Section 10 generator (`scripts/generate_evidence.py`), `evidence/*`, `docs/*`, `README.md`. | Full `pytest -v` + live HTTPS E2E + code review audit |

## Shared Definition of Done Compliance

Each feature increment satisfies all criteria in Proposal Section 7:
1. Requirement and interface contract documented in `contracts/openapi.yaml` and `contracts/schemas/*.schema.json`.
2. Unit, contract, interface, failure-path, and live HTTPS E2E tests pass.
3. Integrated cleanly across independent HTTP/JSON component boundaries (zero cross-component DB access).
4. Reviewed against security, redaction, and contract rules before completion.
5. Demonstrable via both the interactive HTTPS Web UI (`https://127.0.0.1:8440/`) and automated evidence scripts.
