# Section 10 Evidence: Verification & Test Results

Generated: `2026-11-20T15:21:48+08:00`

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
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\Lapizone\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Lapizone\Documents\Sysinteg
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.14.2, langsmith-0.10.12, timeout-2.4.0
timeout: 30.0s
timeout method: thread
timeout func_only: False
collecting ... collected 38 items

tests/contract/test_ct1_eligibility_contract.py::test_ct1_producer_and_consumer_share_eligibility_fixture_and_reject_missing_eligible PASSED [  2%]
tests/contract/test_ct1_eligibility_contract.py::test_directory_handles_ineligible_missing_student_and_validation_errors PASSED [  5%]
tests/contract/test_ct1_eligibility_contract.py::test_directory_enforces_service_credentials_and_rejects_student_sessions PASSED [  7%]
tests/contract/test_ct2_registration_contract.py::test_ct2_confirmed_registration_producer_and_consumer_share_schema PASSED [ 10%]
tests/e2e/test_live_https_stack.py::test_live_https_cluster_full_lifecycle_ui_and_process_kill_fp2 PASSED [ 13%]
tests/e2e/test_ui_clickthrough_smoke.py::test_full_ui_clickthrough_smoke_all_roles_outages_and_responsive PASSED [ 15%]
tests/failure_path/test_dependency_outages_and_security.py::test_rbac_matrix_cross_event_isolation_and_internal_endpoint_protection PASSED [ 18%]
tests/failure_path/test_dependency_outages_and_security.py::test_directory_outage_blocks_registration_with_503_and_creates_no_record PASSED [ 21%]
tests/failure_path/test_fp1_repeated_qr_scan.py::test_fp1_repeated_qr_scan_returns_409_and_preserves_original_record_and_timestamp PASSED [ 23%]
tests/failure_path/test_fp1_repeated_qr_scan.py::test_concurrent_qr_scans_record_at_most_one_checkin_per_registration PASSED [ 26%]
tests/failure_path/test_fp2_attendance_outage.py::test_fp2_attendance_outage_reconciles_and_retries_safely_recording_one_checkin PASSED [ 28%]
tests/failure_path/test_fp2_attendance_outage.py::test_fp2_lost_response_after_commit_discovers_existing_record_and_persistent_outage_stays_unverified PASSED [ 31%]
tests/interface/test_it1_valid_checkin.py::test_it1_valid_checkin_creates_exactly_one_record_and_matches_schema PASSED [ 34%]
tests/interface/test_it1_valid_checkin.py::test_checkin_rejects_wrong_event_or_invalid_token_and_malformed_validation_response PASSED [ 36%]
tests/interface/test_it2_registration_notification.py::test_it2_notification_accepts_request_is_idempotent_and_redacts_logs PASSED [ 39%]
tests/interface/test_it2_registration_notification.py::test_notification_outage_never_reverses_committed_registration_or_checkin PASSED [ 42%]
tests/interface/test_supporting_interfaces.py::test_combined_attendance_report_matches_registrations_and_status_queries PASSED [ 44%]
tests/interface/test_supporting_interfaces.py::test_report_returns_503_instead_of_partial_totals_when_registration_is_unavailable PASSED [ 47%]
tests/unit/test_capacity_and_uniqueness.py::test_duplicate_registration_capacity_limit_and_ineligibility_rules PASSED [ 50%]
tests/unit/test_capacity_and_uniqueness.py::test_concurrent_last_slot_and_duplicate_registration_protected_by_immediate_tx PASSED [ 52%]
tests/unit/test_capacity_and_uniqueness.py::test_stored_registration_never_persists_raw_qr_token_in_plaintext PASSED [ 55%]
tests/unit/test_retry_and_timeouts.py::test_default_timeouts_and_retry_policy_match_proposal_1_to_1 PASSED [ 57%]
tests/unit/test_retry_and_timeouts.py::test_safe_operation_retries_at_most_twice_with_1s_then_2s_delays PASSED [ 60%]
tests/unit/test_retry_and_timeouts.py::test_does_not_retry_4xx_or_unsafe_writes PASSED [ 63%]
tests/unit/test_retry_and_timeouts.py::test_retry_stops_when_overall_deadline_would_be_exceeded PASSED [ 65%]
tests/unit/test_security_and_redaction.py::test_password_hashing_uses_salted_scrypt_and_verifies PASSED [ 68%]
tests/unit/test_security_and_redaction.py::test_qr_token_is_opaque_unpredictable_and_verifier_bound PASSED [ 71%]
tests/unit/test_security_and_redaction.py::test_signed_user_context_rejects_tampering PASSED [ 73%]
tests/unit/test_security_and_redaction.py::test_audit_redaction_strips_qr_tokens_passwords_secrets_and_pii PASSED [ 76%]
tests/unit/test_security_and_redaction.py::test_audit_log_retention_purge_deletes_only_entries_older_than_30_days PASSED [ 78%]
tests/unit/test_time_and_errors.py::test_now_rfc3339_uses_explicit_plus_08_offset PASSED [ 81%]
tests/unit/test_time_and_errors.py::test_format_rfc3339_converts_utc_to_explicit_offset PASSED [ 84%]
tests/unit/test_time_and_errors.py::test_rejects_naive_or_missing_offset_timestamps PASSED [ 86%]
tests/unit/test_time_and_errors.py::test_error_envelope_matches_proposal_contract PASSED [ 89%]
tests/unit/test_validation_and_schemas.py::test_openapi_spec_is_valid_yaml_and_contains_required_paths PASSED [ 92%]
tests/unit/test_validation_and_schemas.py::test_contract_fixtures_validate_against_json_schemas PASSED [ 94%]
tests/unit/test_validation_and_schemas.py::test_dependency_validator_converts_incomplete_response_into_503 PASSED [ 97%]
tests/unit/test_validation_and_schemas.py::test_json_boundary_middleware_enforces_415_400_and_422_with_error_envelope PASSED [100%]

============================== warnings summary ===============================
tests/contract/test_ct2_registration_contract.py: 1 warning
tests/failure_path/test_dependency_outages_and_security.py: 2 warnings
tests/failure_path/test_fp1_repeated_qr_scan.py: 1 warning
tests/failure_path/test_fp2_attendance_outage.py: 2 warnings
tests/interface/test_it1_valid_checkin.py: 2 warnings
tests/interface/test_it2_registration_notification.py: 1 warning
tests/interface/test_supporting_interfaces.py: 1 warning
tests/unit/test_capacity_and_uniqueness.py: 1 warning
  C:\Users\Lapizone\Documents\Sysinteg\services\event_registration\app.py:204: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("shutdown")

tests/contract/test_ct2_registration_contract.py: 1 warning
tests/failure_path/test_dependency_outages_and_security.py: 5 warnings
tests/failure_path/test_fp1_repeated_qr_scan.py: 2 warnings
tests/failure_path/test_fp2_attendance_outage.py: 6 warnings
tests/interface/test_it1_valid_checkin.py: 5 warnings
tests/interface/test_it2_registration_notification.py: 2 warnings
tests/interface/test_supporting_interfaces.py: 3 warnings
tests/unit/test_capacity_and_uniqueness.py: 1 warning
  C:\Users\Lapizone\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\fastapi\applications.py:4599: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    return self.router.on_event(event_type)

tests/failure_path/test_dependency_outages_and_security.py: 1 warning
tests/failure_path/test_fp1_repeated_qr_scan.py: 1 warning
tests/failure_path/test_fp2_attendance_outage.py: 2 warnings
tests/interface/test_it1_valid_checkin.py: 3 warnings
tests/interface/test_it2_registration_notification.py: 1 warning
tests/interface/test_supporting_interfaces.py: 2 warnings
  C:\Users\Lapizone\Documents\Sysinteg\services\attendance\app.py:102: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("shutdown")

tests/failure_path/test_dependency_outages_and_security.py::test_rbac_matrix_cross_event_isolation_and_internal_endpoint_protection
tests/failure_path/test_dependency_outages_and_security.py::test_directory_outage_blocks_registration_with_503_and_creates_no_record
tests/failure_path/test_fp2_attendance_outage.py::test_fp2_attendance_outage_reconciles_and_retries_safely_recording_one_checkin
tests/failure_path/test_fp2_attendance_outage.py::test_fp2_lost_response_after_commit_discovers_existing_record_and_persistent_outage_stays_unverified
  C:\Users\Lapizone\Documents\Sysinteg\services\gateway\app.py:154: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("shutdown")

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================= 38 passed, 50 warnings in 95.56s (0:01:35) ==================
```
