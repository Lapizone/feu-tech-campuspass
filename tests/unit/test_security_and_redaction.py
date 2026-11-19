from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.audit import AuditLogger, redact_payload
from shared.security import (
    compute_token_verifier,
    decrypt_token,
    encrypt_token,
    generate_qr_token,
    hash_password,
    sign_user_context,
    verify_password,
    verify_token_verifier,
    verify_user_context,
)


@pytest.mark.unit
def test_password_hashing_uses_salted_scrypt_and_verifies():
    hashed = hash_password("Password123!")
    assert hashed.startswith("scrypt$")
    assert "Password123!" not in hashed
    assert verify_password("Password123!", hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


@pytest.mark.unit
def test_qr_token_is_opaque_unpredictable_and_verifier_bound():
    from cryptography.fernet import Fernet

    hmac_key = "test-hmac-key-for-unit-test"
    fernet_key = Fernet.generate_key().decode("ascii")

    t1 = generate_qr_token()
    t2 = generate_qr_token()
    assert t1 != t2
    assert len(t1) >= 32
    assert "S-001" not in t1

    verifier = compute_token_verifier(t1, hmac_key)
    assert verifier != t1
    assert verify_token_verifier(t1, verifier, hmac_key) is True
    assert verify_token_verifier(t2, verifier, hmac_key) is False

    ciphertext = encrypt_token(t1, fernet_key)
    assert ciphertext != t1
    assert decrypt_token(ciphertext, fernet_key) == t1


@pytest.mark.unit
def test_signed_user_context_rejects_tampering():
    key = "ctx-signing-secret"
    ctx = {
        "accountId": "ACC-S001",
        "username": "student_s001",
        "role": "STUDENT",
        "studentId": "S-001",
        "assignedEventIds": [],
    }
    signed = sign_user_context(ctx, key)
    assert verify_user_context(signed, key) == ctx

    tampered = signed[:-4] + "AAAA"
    assert verify_user_context(tampered, key) is None
    assert verify_user_context(signed, "wrong-key") is None


@pytest.mark.unit
def test_audit_redaction_strips_qr_tokens_passwords_secrets_and_pii(tmp_path):
    raw = {
        "qrToken": "T-SECRET-999",
        "password": "Password123!",
        "Authorization": "Bearer super-secret-token",
        "name": "Alice Santos",
        "email": "s001@example.test",
        "deliveryDetails": {"qrToken": "T-SECRET-999", "channel": "email"},
        "eventId": "EVT-001",
        "nested": [{"sessionToken": "sess-123", "status": "CONFIRMED"}],
    }
    redacted = redact_payload(raw)
    assert redacted["qrToken"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["name"] == "[REDACTED]"
    assert redacted["email"] == "[REDACTED]"
    assert redacted["deliveryDetails"] == "[REDACTED]"
    assert redacted["nested"][0]["sessionToken"] == "[REDACTED]"
    assert redacted["eventId"] == "EVT-001"
    assert redacted["nested"][0]["status"] == "CONFIRMED"

    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path, service_name="event-registration")
    logger.record(
        correlation_id="corr-123",
        category="Event and registration activity",
        action="REGISTER_STUDENT",
        outcome="SUCCESS",
        actor_id="ACC-S001",
        event_id="EVT-001",
        registration_id="REG-001",
        details={"qrToken": "LEAKED-TOKEN", "email": "s001@example.test", "slots": 1},
    )
    entries = logger.list_logs()
    assert len(entries) == 1
    serialized = str(entries[0])
    assert "LEAKED-TOKEN" not in serialized
    assert "s001@example.test" not in serialized
    assert entries[0]["details"]["slots"] == 1


@pytest.mark.unit
def test_audit_log_retention_purge_deletes_only_entries_older_than_30_days(tmp_path):
    db_path = tmp_path / "audit_retention.db"
    logger = AuditLogger(db_path, service_name="attendance")
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    old_ts = (now - timedelta(days=31)).isoformat()
    recent_ts = (now - timedelta(days=5)).isoformat()

    logger.record(
        correlation_id="corr-old",
        category="Attendance activity",
        action="CHECK_IN",
        outcome="SUCCESS",
        timestamp_override=old_ts,
    )
    logger.record(
        correlation_id="corr-new",
        category="Attendance activity",
        action="CHECK_IN",
        outcome="SUCCESS",
        timestamp_override=recent_ts,
    )

    deleted = logger.purge_expired_logs(retention_days=30, now=now)
    assert deleted == 1
    remaining = logger.list_logs()
    assert len(remaining) == 1
    assert remaining[0]["correlationId"] == "corr-new"
