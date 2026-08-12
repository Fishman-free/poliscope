"""Shared retry-with-backoff policy for outbound calls to external vendors.

Both the Model Gateway (``packages/models/openai_compatible.py``) and the Tool
Gateway (``packages/tools/http_gateway.py``) call a real vendor over HTTP and
need the same answer to "was this failure worth retrying": a dropped
connection or a 429/5xx is the vendor's transient trouble and gets a bounded,
backed-off retry; any other 4xx is our request being wrong for that resource
(a bad DOI, a malformed body) and must surface immediately rather than retry
into a rate limit. Writing that policy twice would let the two gateways drift
apart silently, so it lives here once.

Rate limiting gets its own, harder backoff: a 429 is the vendor saying "do
not call again for a while", not a blip -- hammering a rate-limit window with
a short backoff turns a bounded outage into a retry storm (and a longer
queue). We respect ``Retry-After`` when the vendor sends it and otherwise
back off exponentially up to 60s.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable

import httpx

MAX_TRANSPORT_RETRIES = 3

# 429 (rate limit) backoff ceiling. Longer than the 5xx ceiling on purpose:
# the vendor told us to stop; we stop longer.
_RATE_LIMIT_MAX_BACKOFF = 60.0

# Generic backoff (transport errors / 5xx): short exponential, self-heals
# fast when the vendor recovers.
_GENERIC_MAX_BACKOFF = 8.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse and cap numeric ``Retry-After`` without permitting an unbounded wait."""
    header = response.headers.get("retry-after")
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    return min(max(0.0, seconds), _RATE_LIMIT_MAX_BACKOFF)


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_retries: int = MAX_TRANSPORT_RETRIES,
) -> tuple[httpx.Response, int]:
    """Run ``send``, retrying transport errors and 429/5xx with backoff.

    Returns the first response that is not itself a retryable failure, along
    with how many retries it took. Raises the last error once retries are
    exhausted. Any other 4xx raises immediately via ``raise_for_status`` --
    not transient, so retrying it would only waste the retry budget.

    Backoff is per failure kind: rate limits wait ``Retry-After`` capped at
    60s (or use the same cap exponentially); transport errors and 5xx wait
    up to 8s. Waiting
    happens after a failed attempt, so the first attempt is never delayed.
    """
    retries = 0
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        try:
            response = await send()
        except httpx.TransportError as error:
            last_error = error
            retries += 1
            if retries <= max_retries:
                await asyncio.sleep(min(2**retries * 0.5, _GENERIC_MAX_BACKOFF))
            continue
        if response.status_code == 429:
            last_error = httpx.HTTPStatusError(
                f"upstream returned {response.status_code}",
                request=response.request,
                response=response,
            )
            retries += 1
            if retries <= max_retries:
                retry_after = _retry_after_seconds(response)
                if retry_after is not None:
                    await asyncio.sleep(retry_after)
                else:
                    await asyncio.sleep(
                        min(2**retries * 5.0, _RATE_LIMIT_MAX_BACKOFF)
                    )
            continue
        if response.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                f"upstream returned {response.status_code}",
                request=response.request,
                response=response,
            )
            retries += 1
            if retries <= max_retries:
                await asyncio.sleep(min(2**retries * 0.5, _GENERIC_MAX_BACKOFF))
            continue
        response.raise_for_status()
        return response, retries
    assert last_error is not None
    raise last_error


__all__ = ["MAX_TRANSPORT_RETRIES", "send_with_retry"]
