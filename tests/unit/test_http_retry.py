"""Tests for the shared retry-with-backoff policy.

The policy's contract: transport errors and 429/5xx get bounded, backed-off
retries; other 4xx raise immediately; rate limits wait ``Retry-After`` or
back off harder than generic failures. We patch ``asyncio.sleep`` so the
tests assert the *decision* (how long we chose to wait) without actually
waiting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import httpx
import pytest

from packages.kernel.http_retry import _retry_after_seconds, send_with_retry


def _response(status: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://vendor.example/x"),
                          headers=headers or {})


async def _run(
    responses: list[httpx.Response],
    *,
    max_retries: int = 2,
) -> tuple[tuple[httpx.Response, int], list[float]]:
    """Run the policy against a fixed response sequence; return the outcome
    and the list of sleep durations we chose (patched, not actually slept)."""
    sleeps: list[float] = []
    call_index = 0

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def send() -> httpx.Response:
        nonlocal call_index
        response = responses[call_index]
        call_index += 1
        return response

    with patch("packages.kernel.http_retry.asyncio.sleep", new=fake_sleep):
        result = await send_with_retry(send, max_retries=max_retries)
    return result, sleeps


async def test_429_respects_retry_after_header() -> None:
    # Vendor says "try again in 3 seconds": we wait exactly 3s, then succeed.
    responses = [
        _response(429, headers={"retry-after": "3"}),
        _response(200),
    ]
    (result, retries), sleeps = await _run(responses)
    assert result.status_code == 200
    assert retries == 1
    assert sleeps == [3.0]


async def test_429_without_header_backs_off_exponentially() -> None:
    # No Retry-After: rate-limit backoff is 2**retries * 5, capped at 60s.
    responses = [_response(429), _response(429), _response(200)]
    (_, retries), sleeps = await _run(responses)
    assert retries == 2
    assert sleeps == [10.0, 20.0]


async def test_429_backoff_respects_cap() -> None:
    # Three rate-limited retries: waits 10s, 20s, 40s -- none exceeding the
    # 60s cap, then the 4th call succeeds.
    responses = [_response(429)] * 3 + [_response(200)]
    (result, retries), sleeps = await _run(responses, max_retries=3)
    assert result.status_code == 200
    assert retries == 3
    assert sleeps == [10.0, 20.0, 40.0]
    assert all(seconds <= 60.0 for seconds in sleeps)


async def test_5xx_uses_short_generic_backoff() -> None:
    responses = [_response(503), _response(200)]
    (result, retries), sleeps = await _run(responses)
    assert result.status_code == 200
    assert retries == 1
    assert sleeps == [1.0]  # 2**1 * 0.5


async def test_transport_error_retries_with_generic_backoff() -> None:
    async def send() -> httpx.Response:
        raise httpx.ConnectError("boom")

    with (
        patch("packages.kernel.http_retry.asyncio.sleep", new=asyncio.sleep),
        pytest.raises(httpx.TransportError),
    ):
        await send_with_retry(send, max_retries=1)


async def test_other_4xx_raises_immediately_without_retry() -> None:
    responses = [_response(404), _response(200)]
    with (
        patch("packages.kernel.http_retry.asyncio.sleep", new=asyncio.sleep),
        pytest.raises(httpx.HTTPStatusError) as excinfo,
    ):
        await send_with_retry(_seq_send(responses), max_retries=2)
    assert excinfo.value.response.status_code == 404


def _seq_send(
    responses: list[httpx.Response],
) -> Callable[[], Awaitable[httpx.Response]]:
    """Build a send() that serves a fixed sequence (helper for sync tests)."""
    call_index = 0

    async def send() -> httpx.Response:
        nonlocal call_index
        response = responses[call_index]
        call_index += 1
        return response

    return send


async def test_retry_after_parser_handles_bad_header() -> None:
    bad = _response(429, headers={"retry-after": "not-a-number"})
    zero = _response(429, headers={"retry-after": "0"})
    assert _retry_after_seconds(bad) is None
    assert _retry_after_seconds(zero) == 0.0
    assert _retry_after_seconds(_response(429)) is None
