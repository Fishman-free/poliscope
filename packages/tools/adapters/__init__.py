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


class SearchableSourceAdapter(Protocol):
    """The subset of ``SourceAdapter`` that also exposes free-text search.

    Unpaywall does not implement this -- its real API is DOI-keyed OA-status
    lookup only, with no free-text search endpoint -- so it is deliberately
    absent from ``_SEARCH_ADAPTERS`` below.
    """

    def __init__(self, gateway: ToolGateway, task_id: UUID | None = None) -> None: ...

    async def search(self, query: str) -> NormalizedSource | None: ...


_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "unpaywall": UnpaywallAdapter,
    "semantic_scholar": SemanticScholarAdapter,
}

_SEARCH_ADAPTERS: dict[str, type[SearchableSourceAdapter]] = {
    "openalex": OpenAlexAdapter,
    "crossref": CrossrefAdapter,
    "semantic_scholar": SemanticScholarAdapter,
}

# Attempt order for a free-text (non-DOI) query: first hit wins, a miss or
# error falls through to the next. Keyless and free for all three, so no
# provider here needs a vendor credential to be tried.
SEARCH_ADAPTER_NAMES: tuple[str, ...] = (
    "openalex",
    "crossref",
    "semantic_scholar",
)


def adapter(
    name: str, gateway: ToolGateway, task_id: UUID | None = None
) -> SourceAdapter:
    """Return the named adapter bound to a tool gateway."""
    if name not in _ADAPTERS:
        raise ValueError(f"unknown adapter: {name}")
    return _ADAPTERS[name](gateway, task_id=task_id)


def search_adapter(
    name: str, gateway: ToolGateway, task_id: UUID | None = None
) -> SearchableSourceAdapter:
    """Return the named searchable adapter bound to a tool gateway.

    Raises ``ValueError`` for a name with no search capability (e.g.
    ``"unpaywall"``) rather than silently returning something that would
    fail at call time -- the caller should only ever pass a name from
    ``SEARCH_ADAPTER_NAMES``.
    """
    if name not in _SEARCH_ADAPTERS:
        raise ValueError(f"unknown searchable adapter: {name}")
    return _SEARCH_ADAPTERS[name](gateway, task_id=task_id)


__all__ = [
    "NormalizedSource",
    "SEARCH_ADAPTER_NAMES",
    "SearchableSourceAdapter",
    "SourceAdapter",
    "adapter",
    "search_adapter",
    "CrossrefAdapter",
    "OpenAlexAdapter",
    "SemanticScholarAdapter",
    "UnpaywallAdapter",
]
