# Section 10 Evidence: Sample Request/Response & Redacted Audit Logs

Generated: `2026-11-20T17:36:17+08:00`

All HTTP requests and responses below were captured over HTTPS (`127.0.0.1`) against the running five-service cluster with TLS verification enabled. Raw `qrToken` values, passwords, session credentials, and sensitive PII fields are redacted (`[REDACTED]` / `[REDACTED_QR_TOKEN]`).

### 1. Authenticate student_s001

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-s1`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "student_s001",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:21+08:00",
  "user": {
    "accountId": "ACC-S001",
    "username": "student_s001",
    "role": "STUDENT",
    "studentId": "S-001",
    "assignedEventIds": []
  }
}
```
### 2. Authenticate student_s002

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-s2`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "student_s002",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:21+08:00",
  "user": {
    "accountId": "ACC-S002",
    "username": "student_s002",
    "role": "STUDENT",
    "studentId": "S-002",
    "assignedEventIds": []
  }
}
```
### 3. Authenticate student_s003

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-s3`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "student_s003",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:22+08:00",
  "user": {
    "accountId": "ACC-S003",
    "username": "student_s003",
    "role": "STUDENT",
    "studentId": "S-003",
    "assignedEventIds": []
  }
}
```
### 4. Authenticate student_s004

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-s4`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "student_s004",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:22+08:00",
  "user": {
    "accountId": "ACC-S004",
    "username": "student_s004",
    "role": "STUDENT",
    "studentId": "S-004",
    "assignedEventIds": []
  }
}
```
### 5. Authenticate scanner1

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-scn`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "scanner1",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:22+08:00",
  "user": {
    "accountId": "ACC-SCN1",
    "username": "scanner1",
    "role": "CHECK_IN_STAFF",
    "studentId": null,
    "assignedEventIds": [
      "EVT-001",
      "EVT-002"
    ]
  }
}
```
### 6. Authenticate organizer1

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-org`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "organizer1",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:23+08:00",
  "user": {
    "accountId": "ACC-ORG1",
    "username": "organizer1",
    "role": "EVENT_ORGANIZER",
    "studentId": null,
    "assignedEventIds": [
      "EVT-001",
      "EVT-002"
    ]
  }
}
```
### 7. Authenticate admin1

- **Request**: `POST /api/v1/auth/login` (Correlation ID: `corr-ev-login-adm`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "username": "admin1",
  "password": "[REDACTED]"
}

// Response Body
{
  "sessionToken": "[REDACTED]",
  "expiresAt": "2026-11-21T01:38:23+08:00",
  "user": {
    "accountId": "ACC-ADM1",
    "username": "admin1",
    "role": "ADMINISTRATOR",
    "studentId": null,
    "assignedEventIds": [
      "EVT-001",
      "EVT-002"
    ]
  }
}
```
### 8. Ineligible student registration (422 INELIGIBLE)

- **Request**: `POST /api/v1/events/EVT-001/registrations` (Correlation ID: `corr-ev-ineligible`)
- **HTTP Status**: `422`

```json
// Request Body
{
  "studentId": "S-004"
}

// Response Body
{
  "code": "INELIGIBLE",
  "message": "Student 'S-004' is not eligible for event 'EVT-001'.",
  "correlationId": "corr-ev-ineligible"
}
```
### 9. Eligible S-001 registration (201 CONFIRMED)

- **Request**: `POST /api/v1/events/EVT-001/registrations` (Correlation ID: `corr-ev-reg-s1`)
- **HTTP Status**: `201`

```json
// Request Body
{
  "studentId": "S-001"
}

// Response Body
{
  "registrationId": "REG-CB1F9A46",
  "eventId": "EVT-001",
  "status": "CONFIRMED",
  "qrToken": "[REDACTED]"
}
```
### 10. Duplicate S-001 registration (409 ALREADY_REGISTERED)

- **Request**: `POST /api/v1/events/EVT-001/registrations` (Correlation ID: `corr-ev-reg-dup`)
- **HTTP Status**: `409`

```json
// Request Body
{
  "studentId": "S-001"
}

// Response Body
{
  "code": "ALREADY_REGISTERED",
  "message": "Student 'S-001' is already registered for event 'EVT-001'.",
  "correlationId": "corr-ev-reg-dup"
}
```
### 11. Eligible S-002 registration (201 CONFIRMED)

- **Request**: `POST /api/v1/events/EVT-001/registrations` (Correlation ID: `corr-ev-reg-s2`)
- **HTTP Status**: `201`

```json
// Request Body
{
  "studentId": "S-002"
}

// Response Body
{
  "registrationId": "REG-4FE97E9C",
  "eventId": "EVT-001",
  "status": "CONFIRMED",
  "qrToken": "[REDACTED]"
}
```
### 12. Full event capacity S-003 (409 EVENT_FULL)

- **Request**: `POST /api/v1/events/EVT-001/registrations` (Correlation ID: `corr-ev-reg-full`)
- **HTTP Status**: `409`

```json
// Request Body
{
  "studentId": "S-003"
}

// Response Body
{
  "code": "EVENT_FULL",
  "message": "Event 'EVT-001' has reached its maximum capacity.",
  "correlationId": "corr-ev-reg-full"
}
```
### 13. Validate token for EVT-001

- **Request**: `POST /api/v1/registrations/validate` (Correlation ID: `corr-ev-val-s1`)
- **HTTP Status**: `200`

```json
// Request Body
{
  "eventId": "EVT-001",
  "qrToken": "[REDACTED]"
}

// Response Body
{
  "registrationId": "REG-CB1F9A46",
  "eventId": "EVT-001",
  "status": "CONFIRMED",
  "valid": true
}
```
### 14. Check in S-001 (201 CHECKED_IN)

- **Request**: `POST /api/v1/events/EVT-001/check-ins` (Correlation ID: `corr-ev-chk-s1`)
- **HTTP Status**: `201`

```json
// Request Body
{
  "qrToken": "[REDACTED]"
}

// Response Body
{
  "registrationId": "REG-CB1F9A46",
  "attendanceStatus": "CHECKED_IN",
  "checkInTime": "2026-11-20T17:38:24+08:00"
}
```
### 15. Repeat scan S-001 (409 ALREADY_CHECKED_IN)

- **Request**: `POST /api/v1/events/EVT-001/check-ins` (Correlation ID: `corr-ev-chk-dup`)
- **HTTP Status**: `409`

```json
// Request Body
{
  "qrToken": "[REDACTED]"
}

// Response Body
{
  "code": "ALREADY_CHECKED_IN",
  "message": "Registration 'REG-CB1F9A46' is already checked in for event 'EVT-001'.",
  "correlationId": "corr-ev-chk-dup"
}
```
### 16. Wrong-event scan S-001 at EVT-002 (404 INVALID_TOKEN)

- **Request**: `POST /api/v1/events/EVT-002/check-ins` (Correlation ID: `corr-ev-chk-wrong`)
- **HTTP Status**: `404`

```json
// Request Body
{
  "qrToken": "[REDACTED]"
}

// Response Body
{
  "code": "INVALID_TOKEN",
  "message": "QR token is invalid, unconfirmed, or belongs to a different event.",
  "correlationId": "corr-ev-chk-wrong"
}
```
### 17. Attendance outage during S-002 check-in (503 UNVERIFIED)

- **Request**: `POST /api/v1/events/EVT-001/check-ins` (Correlation ID: `corr-ev-outage`)
- **HTTP Status**: `503`

```json
// Request Body
{
  "qrToken": "[REDACTED]"
}

// Response Body
{
  "code": "SERVICE_UNAVAILABLE",
  "message": "attendance-service is unreachable or timed out.",
  "correlationId": "corr-ev-outage"
}
```
### 18. Authoritative confirmed registrations for EVT-001

- **Request**: `GET /api/v1/events/EVT-001/registrations?status=CONFIRMED` (Correlation ID: `corr-ev-reg-list`)
- **HTTP Status**: `200`

```json
// Request Body
null

// Response Body
{
  "eventId": "EVT-001",
  "registrations": [
    {
      "registrationId": "REG-CB1F9A46",
      "studentId": "S-001",
      "status": "CONFIRMED"
    },
    {
      "registrationId": "REG-4FE97E9C",
      "studentId": "S-002",
      "status": "CONFIRMED"
    }
  ]
}
```
### 19. Authoritative attendance status for REG-S1

- **Request**: `GET /api/v1/events/EVT-001/registrations/REG-CB1F9A46/attendance` (Correlation ID: `corr-ev-att-s1`)
- **HTTP Status**: `200`

```json
// Request Body
null

// Response Body
{
  "eventId": "EVT-001",
  "registrationId": "REG-CB1F9A46",
  "checkedIn": true,
  "checkInTime": "2026-11-20T17:38:24+08:00"
}
```
### 20. Combined attendance report for EVT-001

- **Request**: `GET /api/v1/events/EVT-001/reports/attendance` (Correlation ID: `corr-ev-report`)
- **HTTP Status**: `200`

```json
// Request Body
null

// Response Body
{
  "eventId": "EVT-001",
  "participants": [
    {
      "registrationId": "REG-CB1F9A46",
      "studentId": "S-001",
      "registrationStatus": "CONFIRMED",
      "checkedIn": true,
      "checkInTime": "2026-11-20T17:38:24+08:00"
    },
    {
      "registrationId": "REG-4FE97E9C",
      "studentId": "S-002",
      "registrationStatus": "CONFIRMED",
      "checkedIn": false,
      "checkInTime": null
    }
  ],
  "registeredCount": 2,
  "checkedInCount": 1,
  "notCheckedInCount": 1
}
```

## Aggregated Redacted Audit Logs (`GET /api/v1/admin/audit-logs`)

Unavailable services during capture: `[]`

```json
[
  {
    "logId": "LOG-b631da62d8df",
    "timestamp": "2026-11-20T17:38:31+08:00",
    "correlationId": "corr-ev-report",
    "service": "attendance-service",
    "category": "Notifications and reporting",
    "action": "GENERATE_ATTENDANCE_REPORT",
    "outcome": "SUCCESS",
    "errorCode": null,
    "actorId": "ACC-ORG1",
    "eventId": "EVT-001",
    "registrationId": null,
    "details": {
      "registeredCount": 2,
      "checkedInCount": 1,
      "notCheckedInCount": 1
    }
  },
  {
    "logId": "LOG-676bbfd3a153",
    "timestamp": "2026-11-20T17:38:31+08:00",
    "correlationId": "corr-ev-att-s1",
    "service": "attendance-service",
    "category": "Attendance activity",
    "action": "QUERY_ATTENDANCE_STATUS",
    "outcome": "CHECKED_IN",
    "errorCode": null,
    "actorId": "ACC-SCN1",
    "eventId": "EVT-001",
    "registrationId": "REG-CB1F9A46",
    "details": {}
  },
  {
    "logId": "LOG-7e53f2e768fa",
    "timestamp": "2026-11-20T17:38:26+08:00",
    "correlationId": "corr-ev-outage",
    "service": "gateway",
    "category": "Integration failures",
    "action": "GATEWAY_PROXY",
    "outcome": "UNAVAILABLE",
    "errorCode": "SERVICE_UNAVAILABLE",
    "actorId": "ACC-SCN1",
    "eventId": null,
    "registrationId": null,
    "details": {
      "targetService": "attendance-service",
      "path": "/api/v1/events/EVT-001/check-ins",
      "error": "ConnectError"
    }
  },
  {
    "logId": "LOG-0df9ae04ec20",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-reg-full",
    "service": "student-directory",
    "category": "Event and registration activity",
    "action": "VERIFY_ELIGIBILITY",
    "outcome": "SUCCESS",
    "errorCode": null,
    "actorId": "event-registration",
    "eventId": "EVT-001",
    "registrationId": null,
    "details": {
      "studentId": "S-003",
      "eligible": true,
      "enrollmentStatus": "ENROLLED"
    }
  },
  {
    "logId": "LOG-078e7e98bf40",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-outage",
    "service": "event-registration",
    "category": "Attendance activity",
    "action": "VALIDATE_TOKEN",
    "outcome": "VALID",
    "errorCode": null,
    "actorId": "gateway",
    "eventId": "EVT-001",
    "registrationId": "REG-4FE97E9C",
    "details": {
      "valid": true
    }
  },
  {
    "logId": "LOG-1d923cca5593",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-chk-wrong",
    "service": "event-registration",
    "category": "Attendance activity",
    "action": "VALIDATE_TOKEN",
    "outcome": "INVALID",
    "errorCode": null,
    "actorId": "attendance-service",
    "eventId": "EVT-002",
    "registrationId": null,
    "details": {
      "valid": false
    }
  },
  {
    "logId": "LOG-25dcdff4cb8b",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-chk-dup",
    "service": "event-registration",
    "category": "Attendance activity",
    "action": "VALIDATE_TOKEN",
    "outcome": "VALID",
    "errorCode": null,
    "actorId": "attendance-service",
    "eventId": "EVT-001",
    "registrationId": "REG-CB1F9A46",
    "details": {
      "valid": true
    }
  },
  {
    "logId": "LOG-e5a077df0aa0",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-chk-s1",
    "service": "event-registration",
    "category": "Attendance activity",
    "action": "VALIDATE_TOKEN",
    "outcome": "VALID",
    "errorCode": null,
    "actorId": "attendance-service",
    "eventId": "EVT-001",
    "registrationId": "REG-CB1F9A46",
    "details": {
      "valid": true
    }
  },
  {
    "logId": "LOG-a854e365c032",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-val-s1",
    "service": "event-registration",
    "category": "Attendance activity",
    "action": "VALIDATE_TOKEN",
    "outcome": "VALID",
    "errorCode": null,
    "actorId": "gateway",
    "eventId": "EVT-001",
    "registrationId": "REG-CB1F9A46",
    "details": {
      "valid": true
    }
  },
  {
    "logId": "LOG-05c3df1c864e",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-reg-full",
    "service": "event-registration",
    "category": "Event and registration activity",
    "action": "REGISTER_STUDENT",
    "outcome": "REJECTED",
    "errorCode": "EVENT_FULL",
    "actorId": "ACC-S003",
    "eventId": "EVT-001",
    "registrationId": null,
    "details": {
      "studentId": "S-003"
    }
  },
  {
    "logId": "LOG-1b8d0675429e",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-reg-s2",
    "service": "event-registration",
    "category": "Notifications and reporting",
    "action": "DISPATCH_NOTIFICATION",
    "outcome": "ACCEPTED",
    "errorCode": null,
    "actorId": "event-registration",
    "eventId": "EVT-001",
    "registrationId": "REG-4FE97E9C",
    "details": {
      "type": "REGISTRATION_CONFIRMED",
      "notificationId": "NOTIF-BD596A2C"
    }
  },
  {
    "logId": "LOG-98afe8eb72bf",
    "timestamp": "2026-11-20T17:38:24+08:00",
    "correlationId": "corr-ev-chk-wrong",
    "service": "attendance-service",
    "category": "Attendance activity",
    "action": "CHECK_IN",
    "outcome": "REJECTED",
    "errorCode": "INVALID_TOKEN",
    "actorId": "ACC-SCN1",
    "eventId": "EVT-002",
    "registrationId": null,
    "details": {}
  }
]
```
