# Legacy University Directory Constraint Handling Guide

This guide implements **Section 3 (Revised: Legacy constraint handling)** of the written proposal.

## 1. Prototype Isolation Boundary

The prototype strictly uses synthetic sample records (`contracts/fixtures/sample_seed.json`) stored in `data/student_directory.db`. It does **not** connect to live university authentication providers or production Student Information System (SIS) databases.

Inside `services/student_directory/repository.py`, all lookups sit behind the `LegacyDirectoryAdapter` protocol:

```python
class LegacyDirectoryAdapter(Protocol):
    def get_student_eligibility(self, student_id: str, event_id: str) -> dict[str, Any] | None:
        ...
```

The FastAPI boundary (`services/student_directory/app.py`) and downstream consumers (`Event Registration`) depend **only** on the documented JSON contract (`GET /api/v1/students/{studentId}/eligibility?eventId={eventId}`) and never query SQLite tables outside their own component.

## 2. Pre-Integration Compatibility Assessment Checklist

Before connecting a future implementation to an existing university system, the team and the responsible university system owner must jointly verify:

1. **Identifier Mapping (`studentId`)**:
   - How institutional student numbers, campus IDs, or directory UIDs map to the prototype's stable string `studentId`.
   - Whether normalization (leading zeros, dashes, case) is required at the adapter boundary.
2. **Data Formats & Enumeration Translation**:
   - How legacy enrollment flags (e.g., numeric codes, semester standing strings, hold indicators) translate into the agreed `eligible` (`boolean`) and `enrollmentStatus` (`string`, e.g. `ENROLLED`, `ON_LEAVE`) JSON fields.
   - How student contact fields map to `name` and `email` while keeping RFC 3339 timestamps with explicit offsets (`+08:00`) where applicable.
3. **Supported Interface & Adapter Layer**:
   - Which transport/protocol the legacy system exposes (REST, SOAP, read-only view, or batch feed).
   - Implementing a dedicated `LegacyDirectoryAdapter` subclass that translates requests and preserves exact C1 contract semantics without propagating legacy schemas into Event Registration or Attendance.
4. **Connection Method, Access Restrictions & System Limitations**:
   - Service credential management outside source code and logs.
   - Rate limits, query timeouts (respecting the prototype's `2.0s` internal eligibility budget), and error handling (`404 STUDENT_NOT_FOUND` vs `503 SERVICE_UNAVAILABLE`).
   - Formal sign-off with the university system owner prior to implementation.
