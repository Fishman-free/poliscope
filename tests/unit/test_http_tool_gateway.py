"""Tests for the real HTTP Tool Gateway.

No live network is used -- every request goes through ``httpx.MockTransport``,
matching the same isolation discipline as
``tests/unit/test_openai_compatible_gateway.py``. These tests verify the
gateway's own logic: real-vendor-shaped JSON in, the adapters' flattened
payload shape out (see ``ADAPTER_PAYLOADS`` in
``tests/unit/test_source_adapters.py`` for the contract each adapter expects),
plus config isolation (Unpaywall's required contact email) and retry
behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest

from packages.kernel.contracts import FrozenDict
from packages.tools.adapters import adapter
from packages.tools.contracts import ToolRequest
from packages.tools.http_gateway import (
    CONTACT_EMAIL_ENV,
    HttpToolConfig,
    HttpToolGateway,
    ToolGatewayConfigError,
)


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the retry backoff sleep so transport-retry tests stay fast."""

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr("packages.kernel.http_retry.asyncio.sleep", _instant)


def _request(tool_name: str, doi: str = "10.1234/example") -> ToolRequest:
    return ToolRequest(
        task_id=uuid4(),
        actor="source_adapter",
        tool_name=tool_name,
        operation="lookup_doi",
        arguments=FrozenDict({"doi": doi}),
    )


def _gateway(
    handler: Callable[[httpx.Request], httpx.Response],
    config: HttpToolConfig | None = None,
) -> HttpToolGateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpToolGateway(config, client=client)


async def test_openalex_flattens_authorships_and_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "works/doi:10.1234/example" in str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W1234567890",
                "title": "Digital behavior and wellbeing",
                "publication_year": 2023,
                "type": "article",
                "is_retracted": False,
                "authorships": [
                    {"author": {"display_name": "Smith"}},
                    {"author": {"display_name": "Lee"}},
                ],
            },
        )

    gateway = _gateway(handler)
    result = await gateway.execute(_request("openalex"))
    payload = dict(result.payload)
    assert payload["id"] == "W1234567890"
    assert payload["title"] == "Digital behavior and wellbeing"
    assert payload["authors"] == ("Smith", "Lee")
    assert payload["year"] == 2023
    assert payload["retracted"] is False


async def test_openalex_adapter_consumes_real_shaped_response() -> None:
    """End-to-end: real-vendor JSON -> gateway -> OpenAlexAdapter -> Source."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W1",
                "title": "T",
                "publication_year": 2020,
                "type": "article",
                "is_retracted": False,
                "authorships": [{"author": {"display_name": "Ada"}}],
            },
        )

    gateway = _gateway(handler)
    source = await adapter("openalex", gateway).lookup_doi("10.1234/example")
    assert source.title == "T"
    assert source.authors == ("Ada",)
    assert source.provider_ids["openalex"] == "W1"


async def test_crossref_flattens_message_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "title": ["Digital behavior and wellbeing"],
                    "author": [{"given": "Jane", "family": "Smith"}],
                    "published": {"date-parts": [[2023, 6, 1]]},
                    "type": "journal-article",
                }
            },
        )

    gateway = _gateway(handler)
    result = await gateway.execute(_request("crossref"))
    payload = dict(result.payload)
    assert payload["title"] == "Digital behavior and wellbeing"
    assert payload["authors"] == ("Jane Smith",)
    assert payload["year"] == 2023
    assert payload["type"] == "journal-article"


async def test_crossref_handles_missing_published_date() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"title": ["T"]}})

    gateway = _gateway(handler)
    result = await gateway.execute(_request("crossref"))
    assert dict(result.payload)["year"] is None


async def test_unpaywall_requires_contact_email() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the vendor without a contact email")

    gateway = _gateway(handler)
    with pytest.raises(ToolGatewayConfigError, match=CONTACT_EMAIL_ENV):
        await gateway.execute(_request("unpaywall"))


async def test_unpaywall_sends_email_and_flattens_best_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["email"] == "ops@example.test"
        return httpx.Response(
            200,
            json={
                "oa_status": "gold",
                "best_oa_location": {
                    "url": "https://example.com/fulltext.pdf",
                    "version": "published",
                },
            },
        )

    gateway = _gateway(
        handler, HttpToolConfig(contact_email="ops@example.test")
    )
    result = await gateway.execute(_request("unpaywall"))
    payload = dict(result.payload)
    assert payload["oa_status"] == "gold"
    assert payload["oa_version"] == "published"
    assert payload["url"] == "https://example.com/fulltext.pdf"


async def test_unpaywall_handles_no_oa_location() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"oa_status": "closed", "best_oa_location": None}
        )

    gateway = _gateway(handler, HttpToolConfig(contact_email="ops@example.test"))
    result = await gateway.execute(_request("unpaywall"))
    payload = dict(result.payload)
    assert payload["url"] is None


async def test_semantic_scholar_sends_optional_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "s2-key"
        return httpx.Response(
            200,
            json={
                "paperId": "S1",
                "title": "T",
                "year": 2023,
                "authors": [{"name": "Smith"}, {"name": "Lee"}],
                "publicationTypes": ["JournalArticle"],
            },
        )

    gateway = _gateway(
        handler, HttpToolConfig(semantic_scholar_api_key="s2-key")
    )
    result = await gateway.execute(_request("semantic_scholar"))
    payload = dict(result.payload)
    assert payload["paper_id"] == "S1"
    assert payload["authors"] == ("Smith", "Lee")
    assert payload["publication_types"] == ("JournalArticle",)


async def test_semantic_scholar_works_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-api-key" not in request.headers
        return httpx.Response(
            200, json={"paperId": "S1", "title": "T", "authors": []}
        )

    gateway = _gateway(handler)
    result = await gateway.execute(_request("semantic_scholar"))
    assert dict(result.payload)["paper_id"] == "S1"


async def test_404_propagates_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "not found"})

    gateway = _gateway(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.execute(_request("openalex"))
    assert calls == 1


async def test_retries_on_5xx_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="upstream overloaded")
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W1",
                "title": "T",
                "authorships": [],
                "is_retracted": False,
            },
        )

    gateway = _gateway(handler)
    result = await gateway.execute(_request("openalex"))
    assert calls == 2
    assert result.retries == 1


async def test_transport_error_propagates_after_exhausting_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway = _gateway(handler)
    with pytest.raises(httpx.ConnectError):
        await gateway.execute(_request("openalex"))


async def test_unsupported_operation_raises_config_error() -> None:
    gateway = _gateway(lambda _: httpx.Response(200, json={}))
    request = ToolRequest(
        task_id=uuid4(),
        actor="source_adapter",
        tool_name="openalex",
        operation="search",
        arguments=FrozenDict({"query": "digital wellbeing"}),
    )
    with pytest.raises(ToolGatewayConfigError, match="search"):
        await gateway.execute(request)


async def test_unknown_tool_name_raises_config_error() -> None:
    gateway = _gateway(lambda _: httpx.Response(200, json={}))
    with pytest.raises(ToolGatewayConfigError, match="mystery_vendor"):
        await gateway.execute(_request("mystery_vendor"))
