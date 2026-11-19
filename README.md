# Integrated Campus Event Registration and Attendance System

Prototype implementation matching `Proposal - With In Progress Revisions.docx.pdf` **1:1** across modular service boundaries, versioned `/api/v1/` HTTPS/JSON contracts, RBAC & audit redaction controls, bounded retries/reconciliation, six Scrum sprints, and Section 10 verification evidence.

## Team Members

- Vaughn Djester Caringal
- JT Alexander Escudero
- Jiro Dotillos
- David Marcellus Credo
- Romeo Andree Bundalian

## Architecture (Zero Cross-Component Database Access)

```text
Browser / Clients ──HTTPS──▶ API Gateway & Auth (:8440, data/gateway_auth.db)
                               ├──▶ Student Directory     (:8441, data/student_directory.db)
                               ├──▶ Event Registration    (:8442, data/event_registration.db)
                               │       ├──▶ Student Directory (eligibility)
                               │       └──▶ Notification Service (async 202)
                               ├──▶ Attendance Service    (:8443, data/attendance.db)
                               │       ├──▶ Event Registration (validate & confirmed list)
                               │       └──▶ Notification Service (async 202)
                               └──▶ Notification Service  (:8444, data/notification.db)
```

Each component owns its own SQLite WAL database (`BEGIN IMMEDIATE` + unique constraints) and communicates exclusively via documented HTTPS/JSON interfaces under `/api/v1/` with `X-Correlation-Id` propagation, service-to-service credentials, and signed user context headers.

## Quickstart (Local HTTPS Cluster + Web UI)

1. Bootstrap local TLS certificates (`runtime/certs/`) and cryptographic keys (`runtime/secrets.env`):
   ```powershell
   python scripts/init_runtime.py
   ```
2. Launch all five HTTPS microservices and the local Web Prototype UI:
   ```powershell
   python scripts/run_all.py
   ```
3. Open `https://127.0.0.1:8440/` in your browser (accept the local self-signed development root CA warning or import `runtime/certs/ca.crt`).

### Sample Prototype Accounts

| Username | Password | Role | Notes |
| :--- | :--- | :--- | :--- |
| `student_s001` | `Password123!` | `STUDENT` | `S-001` (`ENROLLED`, eligible for `EVT-001` & `EVT-002`) |
| `student_s002` | `Password123!` | `STUDENT` | `S-002` (`ENROLLED`, eligible) |
| `student_s003` | `Password123!` | `STUDENT` | `S-003` (`ENROLLED`, hits `EVT-001` capacity=2 limit) |
| `student_s004` | `Password123!` | `STUDENT` | `S-004` (`ON_LEAVE` -> `422 INELIGIBLE`) |
| `student_s005` | `Password123!` | `STUDENT` | `S-005` (`ENROLLED`, ineligible for `EVT-001`) |
| `scanner1` | `Password123!` | `CHECK_IN_STAFF` | Assigned to `EVT-001` & `EVT-002` |
| `scanner2` | `Password123!` | `CHECK_IN_STAFF` | Assigned to `EVT-002` only (cross-event RBAC check) |
| `organizer1` | `Password123!` | `EVENT_ORGANIZER` | Assigned to `EVT-001` & `EVT-002` |
| `organizer2` | `Password123!` | `EVENT_ORGANIZER` | Assigned to `EVT-002` only (cross-event RBAC check) |
| `admin1` | `AdminPassword123!` | `ADMINISTRATOR` | Full account, health, notification & audit log access |

## Verification & Section 10 Evidence

- Run the full automated test suite (unit, CT 1–2, IT 1–2, FP 1–2, security/RBAC, and live multi-process HTTPS E2E):
  ```powershell
  python -m pytest -v
  ```
- Regenerate Section 10 evaluation artifacts:
  ```powershell
  python scripts/generate_evidence.py
  ```

### Key Artifacts & Documentation

- OpenAPI 3.1.1 Contract: [contracts/openapi.yaml](contracts/openapi.yaml)
- JSON Schemas & Shared Fixtures: `contracts/schemas/`, `contracts/fixtures/`
- Legacy SIS Compatibility Guide: [docs/legacy-adapter-guide.md](docs/legacy-adapter-guide.md)
- Sprint & Definition of Done Log: [docs/sprint-log.md](docs/sprint-log.md)
- Section 10 Test Results: [evidence/test-results.md](evidence/test-results.md)
- Section 10 Sample Redacted Logs: [evidence/sample-request-response-logs.md](evidence/sample-request-response-logs.md)
- Section 10 Registration vs Attendance Comparison: [evidence/registration-vs-attendance-comparison.md](evidence/registration-vs-attendance-comparison.md)
