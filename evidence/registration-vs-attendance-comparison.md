# Section 10 Evidence: Registration vs Attendance Record Comparison

Generated: `2026-11-20T15:21:48+08:00`

This comparison demonstrates Proposal Section 4 ("Revised: Traceability of integration objectives to project objectives"): Event Registration and Attendance Service maintain separate SQLite databases and reconcile records through documented `/api/v1/` interfaces matched on `(eventId, registrationId)` without manual spreadsheet matching.

## Event Summary (`EVT-001`)

- **Confirmed Registrations (`Event Registration`)**: `2`
- **Report `registeredCount`**: `2`
- **Report `checkedInCount`**: `1`
- **Report `notCheckedInCount`**: `1`

## Matched Participant Records (`eventId=EVT-001`)

| Registration ID | Student ID | Registration Status | Attendance Status | Check-in Time (RFC 3339) |
| :--- | :--- | :--- | :--- | :--- |
| `REG-A87219FF` | `S-001` | `CONFIRMED` | `CHECKED_IN` | `2026-11-20T15:23:44+08:00` |
| `REG-099669FB` | `S-002` | `CONFIRMED` | `NOT_CHECKED_IN` | `null` |

## Raw Interface Payloads

### Event Registration (`GET /api/v1/events/EVT-001/registrations?status=CONFIRMED`)

```json
{
  "eventId": "EVT-001",
  "registrations": [
    {
      "registrationId": "REG-A87219FF",
      "studentId": "S-001",
      "status": "CONFIRMED"
    },
    {
      "registrationId": "REG-099669FB",
      "studentId": "S-002",
      "status": "CONFIRMED"
    }
  ]
}
```

### Attendance Service Combined Report (`GET /api/v1/events/EVT-001/reports/attendance`)

```json
{
  "eventId": "EVT-001",
  "participants": [
    {
      "registrationId": "REG-A87219FF",
      "studentId": "S-001",
      "registrationStatus": "CONFIRMED",
      "checkedIn": true,
      "checkInTime": "2026-11-20T15:23:44+08:00"
    },
    {
      "registrationId": "REG-099669FB",
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
