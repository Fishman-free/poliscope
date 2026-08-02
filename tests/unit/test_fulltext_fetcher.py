"""Tests for the direct full-text PDF fetcher.

No live network is used -- every request goes through ``httpx.MockTransport``,
matching the isolation discipline of ``tests/unit/test_http_tool_gateway.py``
and ``tests/unit/test_openai_compatible_gateway.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import httpx
import pytest

from packages.tools.fulltext_fetcher import (
    MAX_BYTES_ENV,
    TIMEOUT_ENV,
    FullTextFetcher,
    FullTextFetcherConfig,
    FullTextFetchError,
)

PDF_BYTES = b"%PDF-1.4\n%fake minimal pdf body\n%%EOF"


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the retry backoff sleep so transport-retry tests stay fast."""

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr("packages.kernel.http_retry.asyncio.sleep", _instant)


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    config: FullTextFetcherConfig | None = None,
) -> FullTextFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FullTextFetcher(config, client=client)


async def test_fetches_pdf_bytes_and_computes_hash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/paper.pdf"
        return httpx.Response(200, content=PDF_BYTES)

    fetcher = _fetcher(handler)
    result = await fetcher.fetch("https://example.test/paper.pdf")
    assert result.content == PDF_BYTES
    assert result.content_sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.size_bytes == len(PDF_BYTES)
    assert result.retries == 0


async def test_rejects_non_pdf_content() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    fetcher = _fetcher(handler)
    with pytest.raises(FullTextFetchError, match="not a PDF"):
        await fetcher.fetch("https://example.test/paper.pdf")


async def test_rejects_response_over_max_bytes() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_BYTES)

    fetcher = _fetcher(handler, FullTextFetcherConfig(max_bytes=4))
    with pytest.raises(FullTextFetchError, match="exceeds max_bytes"):
        await fetcher.fetch("https://example.test/paper.pdf")


async def test_retries_on_5xx_then_succeeds() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="upstream overloaded")
        return httpx.Response(200, content=PDF_BYTES)

    fetcher = _fetcher(handler)
    result = await fetcher.fetch("https://example.test/paper.pdf")
    assert calls == 2
    assert result.retries == 1


async def test_404_propagates_without_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="not found")

    fetcher = _fetcher(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch("https://example.test/missing.pdf")
    assert calls == 1


async def test_transport_error_propagates_after_exhausting_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    fetcher = _fetcher(handler)
    with pytest.raises(httpx.ConnectError):
        await fetcher.fetch("https://example.test/paper.pdf")


def test_config_reads_only_poliscope_fulltext_env_vars() -> None:
    config = FullTextFetcherConfig.from_env(
        {TIMEOUT_ENV: "12.5", MAX_BYTES_ENV: "1024"}
    )
    assert config.timeout_seconds == 12.5
    assert config.max_bytes == 1024


def test_config_never_falls_back_to_anthropic_session_credentials() -> None:
    """The fetcher must not read this process's own Claude Code credentials.

    It makes unauthenticated GET requests and has no vendor credential of its
    own -- but if a future change ever tried to add one, it must not silently
    pick up ``ANTHROPIC_AUTH_TOKEN``/``ANTHROPIC_BASE_URL`` from the shell
    environment this Claude Code session runs in.
    """
    environ = {
        "ANTHROPIC_AUTH_TOKEN": "sk-session-token-not-a-poliscope-credential",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    }
    config = FullTextFetcherConfig.from_env(environ)
    assert config.timeout_seconds == FullTextFetcherConfig().timeout_seconds
    assert config.max_bytes == FullTextFetcherConfig().max_bytes
