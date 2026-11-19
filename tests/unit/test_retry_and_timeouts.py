from __future__ import annotations

import asyncio

import httpx
import pytest

from shared.config import load_settings
from shared.http_client import ResilientServiceClient


@pytest.mark.unit
def test_default_timeouts_and_retry_policy_match_proposal_1_to_1():
    settings = load_settings(env={})
    assert settings.internal_eligibility_timeout_sec == 2.0
    assert settings.internal_validate_timeout_sec == 2.0
    assert settings.registration_deadline_sec == 5.0
    assert settings.checkin_deadline_sec == 5.0
    assert settings.client_wait_timeout_sec == 8.0
    assert settings.reporting_deadline_sec == 10.0
    assert settings.notification_timeout_sec == 3.0
    assert settings.retry_max_attempts == 2
    assert settings.retry_delays_sec == (1.0, 2.0)
    assert settings.audit_retention_days == 30


@pytest.mark.unit
def test_safe_operation_retries_at_most_twice_with_1s_then_2s_delays():
    calls = 0
    recorded_sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"code": "SERVICE_UNAVAILABLE", "message": "down", "correlationId": "c1"})
        return httpx.Response(200, json={"ok": True})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://mock.local") as async_client:
            client = ResilientServiceClient(
                async_client,
                retry_delays=(1.0, 2.0),
                max_retries=2,
                sleep_fn=fake_sleep,
            )
            resp = await client.request_with_retry(
                "GET",
                "/api/v1/students/S-001/eligibility?eventId=EVT-001",
                timeout_sec=2.0,
                overall_deadline_sec=10.0,
                allow_retry=True,
            )
            assert resp.status_code == 200

    asyncio.run(run())
    assert calls == 3
    assert recorded_sleeps == [1.0, 2.0]


@pytest.mark.unit
def test_does_not_retry_4xx_or_unsafe_writes():
    calls_4xx = 0
    calls_write = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls_4xx, calls_write
        if request.url.path.endswith("/eligibility"):
            calls_4xx += 1
            return httpx.Response(404, json={"code": "STUDENT_NOT_FOUND", "message": "missing", "correlationId": "c1"})
        calls_write += 1
        return httpx.Response(503, json={"code": "SERVICE_UNAVAILABLE", "message": "down", "correlationId": "c2"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://mock.local") as async_client:
            client = ResilientServiceClient(
                async_client,
                retry_delays=(1.0, 2.0),
                max_retries=2,
                sleep_fn=fake_sleep,
            )
            r1 = await client.request_with_retry(
                "GET",
                "/api/v1/students/S-999/eligibility",
                timeout_sec=2.0,
                overall_deadline_sec=5.0,
                allow_retry=True,
            )
            assert r1.status_code == 404

            r2 = await client.request_with_retry(
                "POST",
                "/api/v1/events/EVT-001/check-ins",
                json={"qrToken": "T-001"},
                timeout_sec=5.0,
                overall_deadline_sec=5.0,
                allow_retry=False,
            )
            assert r2.status_code == 503

    asyncio.run(run())
    assert calls_4xx == 1
    assert calls_write == 1
    assert sleeps == []


@pytest.mark.unit
def test_retry_stops_when_overall_deadline_would_be_exceeded():
    calls = 0
    now_value = [0.0]
    recorded_sleeps: list[float] = []

    def fake_clock() -> float:
        return now_value[0]

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)
        now_value[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        now_value[0] += 1.6  # simulates 1.6s per attempt
        return httpx.Response(503, json={"code": "SERVICE_UNAVAILABLE", "message": "down", "correlationId": "c1"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://mock.local") as async_client:
            client = ResilientServiceClient(
                async_client,
                retry_delays=(1.0, 2.0),
                max_retries=2,
                sleep_fn=fake_sleep,
                clock_fn=fake_clock,
            )
            resp = await client.request_with_retry(
                "POST",
                "/api/v1/registrations/validate",
                json={"eventId": "EVT-001", "qrToken": "T-001"},
                timeout_sec=2.0,
                overall_deadline_sec=3.0,
                allow_retry=True,
            )
            assert resp.status_code == 503

    asyncio.run(run())
    # Attempt 1 takes 1.6s -> sleeps 1.0s (elapsed 2.6s) -> Attempt 2 takes 1.6s (elapsed 4.2s > 3.0s deadline -> no 2.0s sleep!)
    assert calls == 2
    assert recorded_sleeps == [1.0]
