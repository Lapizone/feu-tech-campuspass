from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from shared.errors import ServiceError, register_exception_handlers
from shared.middleware import CorrelationIdMiddleware, JsonBoundaryMiddleware
from shared.schemas import assert_schema, load_fixture, validate_dependency_payload


@pytest.mark.unit
def test_openapi_spec_is_valid_yaml_and_contains_required_paths():
    spec_path = Path("contracts/openapi.yaml")
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.1"
    required_paths = [
        "/api/v1/students/{studentId}/eligibility",
        "/api/v1/events/{eventId}/registrations",
        "/api/v1/events/{eventId}/check-ins",
        "/api/v1/notifications",
        "/api/v1/registrations/validate",
        "/api/v1/events/{eventId}/registrations/{registrationId}/attendance",
        "/api/v1/events/{eventId}/reports/attendance",
    ]
    for path in required_paths:
        assert path in spec["paths"], f"Missing required OpenAPI path: {path}"


@pytest.mark.unit
def test_contract_fixtures_validate_against_json_schemas():
    assert_schema("eligibility_response", load_fixture("ct1_eligibility_valid"))
    with pytest.raises(jsonschema.ValidationError):
        assert_schema("eligibility_response", load_fixture("ct1_eligibility_missing_field"))

    assert_schema("registration_response", load_fixture("ct2_registration_confirmed"))
    assert_schema("checkin_response", load_fixture("it1_checkin_valid"))

    it2 = load_fixture("it2_notification_accepted")
    assert_schema("notification_request", it2["request"])
    assert_schema("notification_response", it2["response"])

    errors = load_fixture("error_responses")
    for err_obj in errors.values():
        assert_schema("error", err_obj)


@pytest.mark.unit
def test_dependency_validator_converts_incomplete_response_into_503():
    bad_payload = load_fixture("ct1_eligibility_missing_field")
    with pytest.raises(ServiceError) as exc_info:
        validate_dependency_payload("eligibility_response", bad_payload, "corr-dep-1")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "SERVICE_UNAVAILABLE"


@pytest.mark.unit
def test_json_boundary_middleware_enforces_415_400_and_422_with_error_envelope():
    app = FastAPI()
    app.add_middleware(JsonBoundaryMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    class SampleBody(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        studentId: str = Field(min_length=1)

    @app.post("/api/v1/demo")
    async def demo_endpoint(body: SampleBody):
        return {"studentId": body.studentId}

    client = TestClient(app)

    r_415 = client.post(
        "/api/v1/demo",
        content=b'{"studentId":"S-001"}',
        headers={"Content-Type": "text/plain", "X-Correlation-Id": "corr-415"},
    )
    assert r_415.status_code == 415
    assert r_415.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert r_415.json()["correlationId"] == "corr-415"

    r_400 = client.post(
        "/api/v1/demo",
        content=b'{"studentId": ',
        headers={"Content-Type": "application/json", "X-Correlation-Id": "corr-400"},
    )
    assert r_400.status_code == 400
    assert r_400.json()["code"] == "MALFORMED_REQUEST"
    assert r_400.json()["correlationId"] == "corr-400"

    r_422 = client.post(
        "/api/v1/demo",
        content=json.dumps({"studentId": ""}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Correlation-Id": "corr-422"},
    )
    assert r_422.status_code == 422
    assert r_422.json()["code"] == "INVALID_PAYLOAD"
    assert r_422.json()["correlationId"] == "corr-422"

    # Routing 404 and 405 must also use {code, message, correlationId}
    r_404 = client.get("/api/v1/nonexistent", headers={"X-Correlation-Id": "corr-404"})
    assert r_404.status_code == 404
    assert r_404.json()["code"] == "NOT_FOUND"
    assert r_404.json()["correlationId"] == "corr-404"
    assert_schema("error", r_404.json())

    r_405 = client.get("/api/v1/demo", headers={"X-Correlation-Id": "corr-405"})
    assert r_405.status_code == 405
    assert r_405.json()["code"] == "METHOD_NOT_ALLOWED"
    assert r_405.json()["correlationId"] == "corr-405"
    assert_schema("error", r_405.json())
