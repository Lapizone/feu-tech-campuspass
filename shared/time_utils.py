"""RFC 3339 timestamp formatting and strict offset validation helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RFC3339_OFFSET_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def get_timezone(tz_offset_hours: int = 8) -> timezone:
    """Return a fixed-offset timezone (defaults to +08:00)."""
    return timezone(timedelta(hours=tz_offset_hours))


def format_rfc3339(dt: datetime, tz_offset_hours: int = 8) -> str:
    """Format an aware datetime as RFC 3339 with an explicit timezone offset."""
    target_tz = get_timezone(tz_offset_hours)
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(target_tz).replace(microsecond=0).isoformat()


def now_rfc3339(tz_offset_hours: int = 8) -> str:
    """Return the current timestamp in RFC 3339 format with explicit offset."""
    return datetime.now(get_timezone(tz_offset_hours)).replace(microsecond=0).isoformat()


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC 3339 string and require an explicit offset."""
    if not is_valid_rfc3339_with_offset(value):
        raise ValueError(f"Invalid RFC 3339 timestamp with explicit offset: {value!r}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def is_valid_rfc3339_with_offset(value: str) -> bool:
    """Return True iff value is a valid RFC 3339 timestamp with explicit offset."""
    if not isinstance(value, str) or not _RFC3339_OFFSET_RE.match(value):
        return False
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        return dt.tzinfo is not None
    except ValueError:
        return False
