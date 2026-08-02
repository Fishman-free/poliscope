"""Shared retry-with-backoff policy for outbound calls to external vendors.

Both the Model Gateway (``packages/models/openai_compatible.py``) and the Tool
Gateway (``packages/tools/http_gateway.py``) call a real vendor over HTTP and
need the same answer to "was this failure worth retrying": a dropped
connection or a 429/5xx is the vendor's transient trouble and gets a bounded,
backed-off retry; any other 4xx is our request being wrong for that resource
(a bad DOI, a malformed body) and must surface immediately rather than retry
into a rate limit. Writing that policy twice would let the two gateways drift
apart silently, so it lives here once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx

MAX_TRANSPORT_RETRIES = 3


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
    """
    retries = 0
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            await asyncio.sleep(min(2**attempt * 0.5, 8.0))
        try:
            response = await send()
        except httpx.TransportError as error:
            last_error = error
            retries += 1
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                f"upstream returned {response.status_code}",
                request=response.request,
                response=response,
            )
            retries += 1
            continue
        response.raise_for_status()
        return response, retries
    assert last_error is not None
    raise last_error


__all__ = ["MAX_TRANSPORT_RETRIES", "send_with_retry"]
