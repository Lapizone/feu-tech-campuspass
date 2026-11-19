"""JSON Schema loader and contract/dependency validators."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from shared.errors import ServiceError

_ROOT_DIR = Path(__file__).resolve().parent.parent
_SCHEMAS_DIR = _ROOT_DIR / "contracts" / "schemas"
_FIXTURES_DIR = _ROOT_DIR / "contracts" / "fixtures"


@lru_cache(maxsize=32)
def load_schema(schema_name: str) -> dict[str, Any]:
    """Load a JSON schema from contracts/schemas/<schema_name>.schema.json."""
    filename = schema_name if schema_name.endswith(".json") else f"{schema_name}.schema.json"
    path = _SCHEMAS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=32)
def get_validator(schema_name: str) -> Draft202012Validator:
    """Return a compiled Draft 2020-12 JSON Schema validator."""
    schema = load_schema(schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def assert_schema(schema_name: str, payload: Any) -> None:
    """Validate payload against schema_name, raising jsonschema.ValidationError on mismatch."""
    validator = get_validator(schema_name)
    validator.validate(payload)


def validate_dependency_payload(
    schema_name: str,
    payload: Any,
    correlation_id: str,
) -> Any:
    """Verify downstream dependency response conforms to its contract schema.

    Proposal Section 6 (Input validation): incomplete or unexpected dependency
    responses must never be treated as successful verification, registration,
    or attendance confirmation.
    """
    try:
        assert_schema(schema_name, payload)
    except jsonschema.ValidationError as exc:
        raise ServiceError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message=f"Dependency response failed schema validation ({schema_name}).",
            correlation_id=correlation_id,
        ) from exc
    return payload


def load_fixture(fixture_name: str) -> Any:
    """Load a shared JSON fixture from contracts/fixtures/<fixture_name>.json."""
    filename = fixture_name if fixture_name.endswith(".json") else f"{fixture_name}.json"
    path = _FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))
