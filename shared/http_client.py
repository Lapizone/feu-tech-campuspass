"""Deadline-aware resilient HTTP client enforcing proposal retry rules."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


class DependencyUnavailableError(Exception):
    """Raised when a downstream call fails due to timeout or connection error."""

    def __init__(self, message: str, attempts: int, retry_exhausted: bool) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.retry_exhausted = retry_exhausted


class ResilientServiceClient:
    """Wraps httpx.AsyncClient with strict deadlines and safe-operation retries."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
        max_retries: int = 2,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.retry_delays = retry_delays
        self.max_retries = max_retries
        self.sleep_fn = sleep_fn
        self.clock_fn = clock_fn

    async def request_with_retry(
        self,
        method: str,
        url: str,
        *,
        timeout_sec: float,
        overall_deadline_sec: float,
        allow_retry: bool,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, str] | None = None,
        on_retry: Callable[[int, str], None] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request obeying proposal timeout and retry policy.

        - Retries ONLY when allow_retry=True and ONLY on connection errors,
          timeouts, or HTTP 503.
        - Never retries 4xx client/business errors.
        - Bounded by overall_deadline_sec.
        """
        start = self.clock_fn()
        deadline = start + overall_deadline_sec
        max_attempts = 1 + (self.max_retries if allow_retry else 0)
        last_error: Exception | None = None
        last_response: httpx.Response | None = None

        for attempt in range(max_attempts):
            remaining = deadline - self.clock_fn()
            if remaining <= 0:
                break

            effective_timeout = min(timeout_sec, remaining)
            try:
                response = await self.client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    params=params,
                    timeout=effective_timeout,
                )
                last_response = response
                last_error = None
                if response.status_code != 503 or not allow_retry:
                    return response
                reason = "HTTP 503"
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                last_response = None
                reason = exc.__class__.__name__
                if not allow_retry:
                    raise DependencyUnavailableError(
                        f"Downstream request failed ({reason}).",
                        attempts=attempt + 1,
                        retry_exhausted=False,
                    ) from exc

            if attempt + 1 >= max_attempts:
                break

            delay_idx = min(attempt, len(self.retry_delays) - 1) if self.retry_delays else -1
            delay = self.retry_delays[delay_idx] if delay_idx >= 0 else 0.0
            remaining_after_attempt = deadline - self.clock_fn()
            if remaining_after_attempt <= delay:
                break

            if on_retry is not None:
                on_retry(attempt + 1, reason)
            if delay > 0:
                await self.sleep_fn(delay)

        if last_response is not None:
            return last_response

        raise DependencyUnavailableError(
            f"Downstream dependency unavailable after {max_attempts if allow_retry else 1} attempt(s).",
            attempts=attempt + 1 if "attempt" in locals() else 1,
            retry_exhausted=allow_retry,
        ) from last_error
