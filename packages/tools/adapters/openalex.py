from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from packages.kernel.contracts import FrozenDict
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult

from .normalization import NormalizedSource, normalize_doi


class OpenAlexAdapter:
    """Adapter that queries OpenAlex through the Tool Gateway."""

    def __init__(self, gateway: ToolGateway, task_id: UUID | None = None) -> None:
        self._gateway = gateway
        self._task_id = task_id or uuid4()

    async def lookup_doi(self, doi: str) -> NormalizedSource:
        cleaned = normalize_doi(doi)
        request = ToolRequest(
            task_id=self._task_id,
            actor="source_adapter",
            tool_name="openalex",
            operation="lookup_doi",
            arguments=FrozenDict({"doi": cleaned}),
        )
        result: ToolResult = await self._gateway.execute(request)
        payload = cast(dict[str, Any], result.payload)
        authors = tuple(str(a) for a in payload.get("authors", ()))
        return NormalizedSource(
            doi=cleaned,
            title=str(payload.get("title", "")),
            authors=authors,
            year=cast(int | None, payload.get("year")),
            publication_type=payload.get("type"),
            retracted=bool(payload.get("retracted", False)),
            provider_ids=FrozenDict({"openalex": str(payload.get("id", ""))}),
        )

    async def search(self, query: str) -> NormalizedSource | None:
        """Free-text search, for candidates with no known DOI yet.

        Returns ``None`` on an honest miss rather than fabricating a hit --
        the caller (``SourceAcquisition``) tries the next provider or, if all
        miss, records the query as unresolvable (CLAUDE.md 7).
        """
        request = ToolRequest(
            task_id=self._task_id,
            actor="source_adapter",
            tool_name="openalex",
            operation="search",
            arguments=FrozenDict({"query": query}),
        )
        result: ToolResult = await self._gateway.execute(request)
        payload = cast(dict[str, Any], result.payload)
        doi = payload.get("doi")
        if not isinstance(doi, str) or not doi:
            return None
        cleaned = normalize_doi(doi)
        authors = tuple(str(a) for a in payload.get("authors", ()))
        return NormalizedSource(
            doi=cleaned,
            title=str(payload.get("title", "")),
            authors=authors,
            year=cast(int | None, payload.get("year")),
            publication_type=payload.get("type"),
            retracted=bool(payload.get("retracted", False)),
            provider_ids=FrozenDict({"openalex": str(payload.get("id", ""))}),
        )
