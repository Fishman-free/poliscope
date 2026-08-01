from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from packages.kernel.contracts import FrozenDict
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult

from .normalization import NormalizedSource, normalize_doi


class UnpaywallAdapter:
    """Adapter that queries Unpaywall for OA status and controlled URLs."""

    def __init__(self, gateway: ToolGateway, task_id: UUID | None = None) -> None:
        self._gateway = gateway
        self._task_id = task_id or uuid4()

    async def lookup_doi(self, doi: str) -> NormalizedSource:
        cleaned = normalize_doi(doi)
        request = ToolRequest(
            task_id=self._task_id,
            actor="source_adapter",
            tool_name="unpaywall",
            operation="lookup_doi",
            arguments=FrozenDict({"doi": cleaned}),
        )
        result: ToolResult = await self._gateway.execute(request)
        payload = cast(dict[str, Any], result.payload)
        url = payload.get("url")
        urls = (str(url),) if url else ()
        return NormalizedSource(
            doi=cleaned,
            title="",
            authors=(),
            year=None,
            publication_type=None,
            retracted=False,
            provider_ids=FrozenDict({"unpaywall": cleaned}),
            oa_status=payload.get("oa_status"),
            oa_version=payload.get("oa_version"),
            controlled_fulltext_urls=urls,
        )
