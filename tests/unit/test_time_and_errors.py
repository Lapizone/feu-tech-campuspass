from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.errors import ServiceError, error_payload
from shared.time_utils import format_rfc3339, is_valid_rfc3339_with_offset, now_rfc3339


@pytest.mark.unit
def test_now_rfc3339_uses_explicit_plus_08_offset():
    ts = now_rfc3339()
    assert ts.endswith("+08:00")
    assert is_valid_rfc3339_with_offset(ts) is True


@pytest.mark.unit
def test_format_rfc3339_converts_utc_to_explicit_offset():
    dt = datetime(2026, 10, 1, 1, 0, 0, tzinfo=timezone.utc)
    formatted = format_rfc3339(dt, tz_offset_hours=8)
    assert formatted == "2026-10-01T09:00:00+08:00"
    assert is_valid_rfc3339_with_offset(formatted) is True


@pytest.mark.unit
def test_rejects_naive_or_missing_offset_timestamps():
    assert is_valid_rfc3339_with_offset("2026-10-01T09:00:00") is False
    assert is_valid_rfc3339_with_offset("2026-10-01 09:00:00") is False
    assert is_valid_rfc3339_with_offset("not-a-timestamp") is False
    assert is_valid_rfc3339_with_offset("2026-10-01T09:00:00+08:00") is True


@pytest.mark.unit
def test_error_envelope_matches_proposal_contract():
    payload = error_payload(
        code="ALREADY_CHECKED_IN",
        message="Registration has already been checked in.",
        correlation_id="corr-abc123",
    )
    assert payload == {
        "code": "ALREADY_CHECKED_IN",
        "message": "Registration has already been checked in.",
        "correlationId": "corr-abc123",
    }

    err = ServiceError(409, "EVENT_FULL", "Event capacity reached.", "corr-xyz")
    assert err.status_code == 409
    assert err.to_dict() == {
        "code": "EVENT_FULL",
        "message": "Event capacity reached.",
        "correlationId": "corr-xyz",
    }
