"""Tool Gateway backed by real calls to OpenAlex, Crossref, Unpaywall, and
Semantic Scholar.

The four adapters in ``packages/tools/adapters/`` already speak a flattened,
provider-agnostic payload shape (``title``, ``authors`` as a tuple of plain
names, ``year``, ...) and route every call through the ``ToolGateway``
protocol -- CLAUDE.md 8 forbids a vendor HTTP call inside an adapter. Until
now the only implementation of that protocol was ``RecordedToolGateway``, so
every real lookup was an unfillable gap. This is the first gateway that
actually reaches the vendors; the adapters and everything above them are
unchanged.

**Why these four need no vendor credential.** OpenAlex, Crossref, and
Semantic Scholar's base search/lookup endpoints are public and keyless.
Unpaywall's terms require an identifying ``email`` query parameter on every
call (no key, just an email in its usage policy) -- that is
``POLISCOPE_TOOLS_CONTACT_EMAIL`` below, required only for the Unpaywall
operation so the other three adapters keep working with nothing configured.
Semantic Scholar accepts an optional API key for a higher rate limit; without
one, requests still succeed, just at the anonymous rate.

**Failure shape.** A DOI the vendor has never heard of is a 404, which is not
transient -- ``send_with_retry`` (``packages/kernel/http_retry.py``) raises it
immediately rather than retrying into a rate limit, and it propagates out of
``execute`` as an ``httpx.HTTPStatusError``. ``SourceAcquisition.acquire``
already wraps each adapter call in a ``try/except`` and records that as a
refused candidate (see ``packages/papers/acquisition.py``), so no change was
needed there to make a real 404 behave the same as the recorded gateway's
"no recording for this hash" ``RecordingNotFound``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from packages.kernel.contracts import FrozenDict
from packages.kernel.http_retry import send_with_retry
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult

CONTACT_EMAIL_ENV = "POLISCOPE_TOOLS_CONTACT_EMAIL"
SEMANTIC_SCHOLAR_API_KEY_ENV = "POLISCOPE_TOOLS_SEMANTIC_SCHOLAR_API_KEY"
TIMEOUT_ENV = "POLISCOPE_TOOLS_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 30.0

OPENALEX_BASE_URL = "https://api.openalex.org"
CROSSREF_BASE_URL = "https://api.crossref.org"
UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"

SEMANTIC_SCHOLAR_FIELDS = "paperId,title,year,authors,publicationTypes"
SEMANTIC_SCHOLAR_SEARCH_FIELDS = (
    "paperId,title,year,authors,publicationTypes,externalIds,citationCount"
)

# Providers with a real, keyless, free-text search endpoint. Unpaywall's real
# API has no such endpoint -- it is DOI-keyed OA-status lookup only -- so it
# deliberately has no entry here and no ``_search_unpaywall`` method below.
SEARCHABLE_TOOL_NAMES: frozenset[str] = frozenset(
    {"openalex", "crossref", "semantic_scholar"}
)


class ToolGatewayConfigError(ValueError):
    """Raised when an operation is asked for without the config it needs.

    A missing ``POLISCOPE_TOOLS_CONTACT_EMAIL`` only breaks the Unpaywall
    operation, so it is raised lazily, on that call, rather than at
    construction -- the other three adapters must keep working unconfigured.
    """


@dataclass(frozen=True, slots=True)
class HttpToolConfig:
    contact_email: str | None = None
    semantic_scholar_api_key: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HttpToolConfig:
        values = os.environ if environ is None else environ
        timeout = float(values.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_SECONDS)
        return cls(
            contact_email=values.get(CONTACT_EMAIL_ENV) or None,
            semantic_scholar_api_key=values.get(SEMANTIC_SCHOLAR_API_KEY_ENV) or None,
            timeout_seconds=timeout,
        )


class HttpToolGateway:
    """Satisfies ``ToolGateway`` against the four real discovery vendors."""

    def __init__(
        self,
        config: HttpToolConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or HttpToolConfig()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._config.timeout_seconds)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HttpToolGateway:
        return cls(HttpToolConfig.from_env(environ))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute(self, request: ToolRequest) -> ToolResult:
        started = time.monotonic()
        if request.operation == "lookup_doi":
            doi = request.arguments.get("doi")
            if not isinstance(doi, str) or not doi:
                raise ToolGatewayConfigError(
                    f"{request.tool_name}.lookup_doi requires a string 'doi' argument"
                )
            payload, retries = await self._lookup_doi(request.tool_name, doi)
        elif request.operation == "search":
            query = request.arguments.get("query")
            if not isinstance(query, str) or not query:
                raise ToolGatewayConfigError(
                    f"{request.tool_name}.search requires a string 'query' argument"
                )
            payload, retries = await self._search(request.tool_name, query)
        else:
            raise ToolGatewayConfigError(
                f"no HTTP implementation for {request.tool_name}.{request.operation}"
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            latency_ms=latency_ms,
            retries=retries,
            error_code=None,
        )

    async def _lookup_doi(
        self, tool_name: str, doi: str
    ) -> tuple[dict[str, object], int]:
        if tool_name == "openalex":
            return await self._openalex(doi)
        if tool_name == "crossref":
            return await self._crossref(doi)
        if tool_name == "unpaywall":
            return await self._unpaywall(doi)
        if tool_name == "semantic_scholar":
            return await self._semantic_scholar(doi)
        raise ToolGatewayConfigError(f"no HTTP implementation for tool {tool_name!r}")

    async def _search(
        self, tool_name: str, query: str
    ) -> tuple[dict[str, object], int]:
        if tool_name == "openalex":
            return await self._search_openalex(query)
        if tool_name == "crossref":
            return await self._search_crossref(query)
        if tool_name == "semantic_scholar":
            return await self._search_semantic_scholar(query)
        # Deliberately includes unpaywall: its real API has no free-text
        # search, only DOI-keyed OA-status lookup (see SEARCHABLE_TOOL_NAMES).
        raise ToolGatewayConfigError(f"no HTTP implementation for {tool_name}.search")

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        response, retries = await send_with_retry(
            lambda: self._client.get(url, params=params, headers=headers)
        )
        data: dict[str, Any] = response.json()
        return data, retries

    async def _openalex(self, doi: str) -> tuple[dict[str, object], int]:
        data, retries = await self._get_json(f"{OPENALEX_BASE_URL}/works/doi:{doi}")
        authorships = data.get("authorships") or []
        authors = tuple(
            str(a["author"]["display_name"])
            for a in authorships
            if a.get("author", {}).get("display_name")
        )
        work_id = str(data.get("id") or "").rsplit("/", maxsplit=1)[-1]
        payload: dict[str, object] = {
            "id": work_id,
            "title": data.get("title") or data.get("display_name") or "",
            "authors": authors,
            "year": data.get("publication_year"),
            "type": data.get("type"),
            "retracted": bool(data.get("is_retracted", False)),
        }
        return payload, retries

    async def _crossref(self, doi: str) -> tuple[dict[str, object], int]:
        data, retries = await self._get_json(f"{CROSSREF_BASE_URL}/works/{doi}")
        message = data.get("message") or {}
        titles = message.get("title") or []
        def _full_name(author: Mapping[str, Any]) -> str:
            parts = (author.get("given"), author.get("family"))
            return " ".join(part for part in parts if part).strip()

        raw_authors = (_full_name(a) for a in message.get("author") or [])
        authors = tuple(name for name in raw_authors if name)
        date_parts = ((message.get("published") or {}).get("date-parts") or [[]])[0]
        payload: dict[str, object] = {
            "title": titles[0] if titles else "",
            "authors": authors,
            "year": date_parts[0] if date_parts else None,
            "type": message.get("type"),
        }
        return payload, retries

    async def _unpaywall(self, doi: str) -> tuple[dict[str, object], int]:
        if not self._config.contact_email:
            raise ToolGatewayConfigError(
                f"Missing required environment variable: {CONTACT_EMAIL_ENV}. "
                "Unpaywall's usage policy requires an identifying contact "
                "email on every request."
            )
        data, retries = await self._get_json(
            f"{UNPAYWALL_BASE_URL}/{doi}",
            params={"email": self._config.contact_email},
        )
        best = data.get("best_oa_location") or {}
        payload: dict[str, object] = {
            "oa_status": data.get("oa_status"),
            "oa_version": best.get("version"),
            "url": best.get("url") or best.get("url_for_pdf"),
        }
        return payload, retries

    async def _semantic_scholar(self, doi: str) -> tuple[dict[str, object], int]:
        headers = (
            {"x-api-key": self._config.semantic_scholar_api_key}
            if self._config.semantic_scholar_api_key
            else None
        )
        data, retries = await self._get_json(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/DOI:{doi}",
            params={"fields": SEMANTIC_SCHOLAR_FIELDS},
            headers=headers,
        )
        authors = tuple(
            str(a["name"]) for a in data.get("authors") or [] if a.get("name")
        )
        payload: dict[str, object] = {
            "paper_id": data.get("paperId") or "",
            "title": data.get("title") or "",
            "authors": authors,
            "year": data.get("year"),
            "publication_types": tuple(data.get("publicationTypes") or ()),
        }
        return payload, retries

    async def _search_openalex(self, query: str) -> tuple[dict[str, object], int]:
        # Round-4 authority: sort free-text hits by citation count so the
        # first hit is the most-cited (most authoritative) work matching the
        # query, not merely the most textually relevant one.
        data, retries = await self._get_json(
            f"{OPENALEX_BASE_URL}/works",
            params={
                "search": query,
                "per-page": "1",
                "sort": "cited_by_count:desc",
            },
        )
        results = data.get("results") or []
        if not results:
            # Honest miss: this provider found no candidate for this query.
            # SourceAcquisition tries the next provider before giving up.
            return {"doi": None}, retries
        work = results[0]
        authorships = work.get("authorships") or []
        authors = tuple(
            str(a["author"]["display_name"])
            for a in authorships
            if a.get("author", {}).get("display_name")
        )
        work_id = str(work.get("id") or "").rsplit("/", maxsplit=1)[-1]
        raw_doi = str(work.get("doi") or "")
        doi = raw_doi.rsplit("doi.org/", maxsplit=1)[-1] if raw_doi else None
        payload: dict[str, object] = {
            "doi": doi,
            "id": work_id,
            "title": work.get("title") or work.get("display_name") or "",
            "authors": authors,
            "year": work.get("publication_year"),
            "type": work.get("type"),
            "retracted": bool(work.get("is_retracted", False)),
            "citation_count": int(work.get("cited_by_count") or 0),
        }
        return payload, retries

    async def _search_crossref(self, query: str) -> tuple[dict[str, object], int]:
        data, retries = await self._get_json(
            f"{CROSSREF_BASE_URL}/works",
            params={"query": query, "rows": "1"},
        )
        items = (data.get("message") or {}).get("items") or []
        if not items:
            return {"doi": None}, retries
        item = items[0]
        titles = item.get("title") or []

        def _full_name(author: Mapping[str, Any]) -> str:
            parts = (author.get("given"), author.get("family"))
            return " ".join(part for part in parts if part).strip()

        raw_authors = (_full_name(a) for a in item.get("author") or [])
        authors = tuple(name for name in raw_authors if name)
        date_parts = ((item.get("published") or {}).get("date-parts") or [[]])[0]
        payload: dict[str, object] = {
            "doi": item.get("DOI"),
            "title": titles[0] if titles else "",
            "authors": authors,
            "year": date_parts[0] if date_parts else None,
            "type": item.get("type"),
            "citation_count": int(item.get("is-referenced-by-count") or 0),
        }
        return payload, retries

    async def _search_semantic_scholar(
        self, query: str
    ) -> tuple[dict[str, object], int]:
        headers = (
            {"x-api-key": self._config.semantic_scholar_api_key}
            if self._config.semantic_scholar_api_key
            else None
        )
        # Round-4 authority: sort by citation count, same rationale as the
        # OpenAlex sort above.
        data, retries = await self._get_json(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search",
            params={
                "query": query,
                "fields": SEMANTIC_SCHOLAR_SEARCH_FIELDS,
                "limit": "1",
                "sort": "citationCount:desc",
            },
            headers=headers,
        )
        papers = data.get("data") or []
        if not papers:
            return {"doi": None}, retries
        paper = papers[0]
        authors = tuple(
            str(a["name"]) for a in paper.get("authors") or [] if a.get("name")
        )
        external_ids = paper.get("externalIds") or {}
        payload: dict[str, object] = {
            "doi": external_ids.get("DOI"),
            "paper_id": paper.get("paperId") or "",
            "title": paper.get("title") or "",
            "authors": authors,
            "year": paper.get("year"),
            "publication_types": tuple(paper.get("publicationTypes") or ()),
            "citation_count": int(paper.get("citationCount") or 0),
        }
        return payload, retries


def tool_gateway_from_env(
    environ: Mapping[str, str] | None = None,
) -> HttpToolGateway:
    """Build the gateway from environment config.

    Unlike the Model Gateway, there is no vendor credential that can be
    "unset" here -- OpenAlex, Crossref, and Semantic Scholar work with no
    configuration at all, so this always returns a working gateway. Only the
    Unpaywall operation raises, and only when it is actually called without
    ``POLISCOPE_TOOLS_CONTACT_EMAIL`` set.
    """
    return HttpToolGateway.from_env(environ)


__all__: list[str] = [
    "CONTACT_EMAIL_ENV",
    "SEMANTIC_SCHOLAR_API_KEY_ENV",
    "SEARCHABLE_TOOL_NAMES",
    "HttpToolConfig",
    "HttpToolGateway",
    "ToolGateway",
    "ToolGatewayConfigError",
    "tool_gateway_from_env",
]
