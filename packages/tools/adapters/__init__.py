from __future__ import annotations

from typing import Protocol
from uuid import UUID

from packages.tools.contracts import ToolGateway

from .crossref import CrossrefAdapter
from .normalization import NormalizedSource
from .openalex import OpenAlexAdapter
from .semantic_scholar import SemanticScholarAdapter
from .unpaywall import UnpaywallAdapter


class SourceAdapter(Protocol):
    """Protocol for scholarly discovery source adapters."""

    def __init__(self, gateway: ToolGateway, task_id: UUID | None = None) -> None: ...

    async def lookup_doi(self, doi: str) -> NormalizedSource: ...


_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "unpaywall": UnpaywallAdapter,
    "semantic_scholar": SemanticScholarAdapter,
}


def adapter(
    name: str, gateway: ToolGateway, task_id: UUID | None = None
) -> SourceAdapter:
    """Return the named adapter bound to a tool gateway."""
    if name not in _ADAPTERS:
        raise ValueError(f"unknown adapter: {name}")
    return _ADAPTERS[name](gateway, task_id=task_id)


__all__ = [
    "NormalizedSource",
    "SourceAdapter",
    "adapter",
    "CrossrefAdapter",
    "OpenAlexAdapter",
    "SemanticScholarAdapter",
    "UnpaywallAdapter",
]
