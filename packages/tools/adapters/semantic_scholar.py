from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from packages.kernel.contracts import FrozenDict
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult

from .normalization import NormalizedSource, normalize_doi


class SemanticScholarAdapter:
    """Adapter that queries Semantic Scholar through the Tool Gateway."""

    def __init__(self, gateway: ToolGateway, task_id: UUID | None = None) -> None:
        self._gateway = gateway
        self._task_id = task_id or uuid4()

    async def lookup_doi(self, doi: str) -> NormalizedSource:
        cleaned = normalize_doi(doi)
        request = ToolRequest(
            task_id=self._task_id,
            actor="source_adapter",
            tool_name="semantic_scholar",
            operation="lookup_doi",
            arguments=FrozenDict({"doi": cleaned}),
        )
        result: ToolResult = await self._gateway.execute(request)
        payload = cast(dict[str, Any], result.payload)
        authors = tuple(str(a) for a in payload.get("authors", ()))
        pub_types = payload.get("publication_types") or payload.get("publicationTypes")
        publication_type = pub_types[0] if pub_types else None
        return NormalizedSource(
            doi=cleaned,
            title=str(payload.get("title", "")),
            authors=authors,
            year=cast(int | None, payload.get("year")),
            publication_type=publication_type,
            retracted=False,
            citation_count=int(payload.get("citation_count", 0) or 0),
            provider_ids=FrozenDict(
                {"semantic_scholar": str(payload.get("paper_id", ""))}
            ),
        )

    async def search(self, query: str) -> NormalizedSource | None:
        """Free-text search; returns ``None`` on an honest miss (CLAUDE.md 7)."""
        request = ToolRequest(
            task_id=self._task_id,
            actor="source_adapter",
            tool_name="semantic_scholar",
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
        pub_types = payload.get("publication_types") or payload.get("publicationTypes")
        publication_type = pub_types[0] if pub_types else None
        return NormalizedSource(
            doi=cleaned,
            title=str(payload.get("title", "")),
            authors=authors,
            year=cast(int | None, payload.get("year")),
            publication_type=publication_type,
            retracted=False,
            citation_count=int(payload.get("citation_count", 0) or 0),
            provider_ids=FrozenDict(
                {"semantic_scholar": str(payload.get("paper_id", ""))}
            ),
        )
