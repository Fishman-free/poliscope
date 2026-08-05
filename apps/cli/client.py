"""HTTP client for the Poliscope API.

The CLI is a thin adapter over the same endpoints the web workspace uses. It
must not reach into ``packages`` directly: a second code path into the research
service would let the CLI bypass the Evidence Gate, which CLAUDE.md 5.3 forbids.
Every method here maps to exactly one HTTP route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"

# A research task can run for tens of minutes, but no single HTTP call should.
# The stream endpoint is excluded because it stays open by design.
DEFAULT_TIMEOUT = 30.0

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class APIError(RuntimeError):
    """The API answered, but rejected the request.

    Carries the status code so the caller can map it onto an exit code without
    parsing the message.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class APIUnreachable(RuntimeError):
    """The API could not be contacted, so the request never reached the server."""


class CLIClient:
    """One client instance per CLI invocation."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: httpx.BasicAuth | None = None,
    ) -> None:
        """``transport`` exists so a test can assert which URL was requested.

        Stubbing the client's own methods proves only that the CLI passes
        arguments around; it cannot catch a route that does not exist, which is
        how ``export`` shipped pointing at a path the API never served.

        ``auth`` exists for the same reason a deployed instance can sit behind
        Caddy's shared HTTP Basic Auth (see ``deploy/caddy/Caddyfile``): a CLI
        or Skill invocation pointed at that instance needs to send the same
        credentials a browser would, or every request 401s before it reaches
        the API at all. ``None`` (the default) preserves today's unauthenticated
        behaviour for a local, un-gated API.
        """
        self.base_url = base_url.rstrip("/")
        # A developer machine often exports HTTP_PROXY for outbound traffic. Sending
        # loopback requests through that proxy makes a not-yet-started API look like
        # a broken one, because the proxy answers with its own error status instead
        # of the connection being refused. Remote hosts still honour the proxy.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            trust_env=not _is_loopback(self.base_url),
            transport=transport,
            auth=auth,
        )

    async def __aenter__(self) -> CLIClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.RequestError as error:
            raise APIUnreachable(
                f"cannot reach the Poliscope API at {self.base_url}: {error}"
            ) from error
        if response.is_error:
            raise APIError(response.status_code, _extract_detail(response))
        decoded: dict[str, Any] = response.json()
        return decoded

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def create_task(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/tasks", json=contract)

    async def confirm_claims(
        self,
        task_id: str,
        claim_ids: Iterable[str],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/tasks/{task_id}/confirm-claims",
            json={"claim_ids": list(claim_ids)},
        )

    async def workspace(self, task_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/workspace/{task_id}")

    async def pause(self, task_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/tasks/{task_id}/pause")

    async def resume(self, task_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/tasks/{task_id}/resume")

    async def council_preview(self, task_id: str) -> dict[str, Any]:
        """Read the 7 seats' BLINDSPOT_BOUNTY-end positions while halted.

        Plan phase 8.4. Maps onto ``GET /api/tasks/{id}/council-preview``,
        which itself is a read-only reuse of the workspace panel's own
        per-seat aggregation -- see apps/api/routers/tasks.py.
        """
        return await self._request("GET", f"/api/tasks/{task_id}/council-preview")

    async def council_guidance(
        self,
        task_id: str,
        guidance_text: str,
    ) -> dict[str, Any]:
        """Submit the human's advisory steer (or ``""`` for no intervention).

        Plan phase 8.4. CLAUDE.md 4/8 forbid this from ever deciding
        scientific truth, so an empty string is as valid an answer as a real
        steer -- see ``CouncilGuidanceRequest`` in apps/api/schemas.py.
        """
        return await self._request(
            "POST",
            f"/api/tasks/{task_id}/council-guidance",
            json={"guidance_text": guidance_text},
        )

    async def export(self, task_id: str, export_format: str) -> str:
        """Fetch the research brief in the requested format.

        Returns text rather than a decoded object because ``format=markdown``
        answers with ``text/markdown``. This previously pointed at
        ``/api/tasks/{id}/export``, an endpoint that has never existed, so every
        export ended in a 404 -- and the CLI's own tests asserted only that the
        client formed a request, never that the path was real.
        """
        try:
            response = await self._client.get(
                f"/api/reports/{task_id}", params={"format": export_format}
            )
        except httpx.RequestError as error:
            raise APIUnreachable(
                f"cannot reach the Poliscope API at {self.base_url}: {error}"
            ) from error
        if response.is_error:
            raise APIError(response.status_code, _extract_detail(response))
        return response.text

    async def watch(
        self,
        task_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """Yield decoded SSE frames until the server closes the stream.

        ``Last-Event-ID`` is a request header rather than a query parameter so
        that a resumed watch is indistinguishable from a browser reconnect and
        exercises the same server code path.
        """
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        try:
            async with self._client.stream(
                "GET",
                f"/api/stream/{task_id}",
                headers=headers,
                timeout=None,
            ) as response:
                if response.is_error:
                    await response.aread()
                    raise APIError(response.status_code, _extract_detail(response))
                frame: dict[str, str] = {}
                async for line in response.aiter_lines():
                    if line == "":
                        if frame:
                            yield frame
                            frame = {}
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    frame[field.strip()] = value.lstrip()
                if frame:
                    yield frame
        except httpx.RequestError as error:
            raise APIUnreachable(
                f"cannot reach the Poliscope API at {self.base_url}: {error}"
            ) from error


def _is_loopback(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "") in LOOPBACK_HOSTS


def _extract_detail(response: httpx.Response) -> str:
    """Prefer FastAPI's ``detail`` field, falling back to the raw body."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return response.text.strip() or f"HTTP {response.status_code}"
