from __future__ import annotations

import re

from packages.kernel.contracts import ContractModel, FrozenDict


def normalize_doi(doi: str) -> str:
    """Strip protocol, lowercase, and trim whitespace from a DOI."""
    cleaned = doi.strip().lower()
    cleaned = re.sub(r"^https?://(dx\.)?doi\.org/", "", cleaned)
    return cleaned


class NormalizedSource(ContractModel):
    """Normalized representation of a scholarly source across providers."""

    doi: str
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    publication_type: str | None = None
    retracted: bool = False
    provider_ids: FrozenDict[str, str] = FrozenDict()
    metadata_conflicts: FrozenDict[str, tuple[str, ...]] = FrozenDict()
    oa_status: str | None = None
    oa_version: str | None = None
    controlled_fulltext_urls: tuple[str, ...] = ()
    # How many later works cite this one, per the provider that answered.
    # Displayed on the live view's literature cards as a coarse authority
    # signal (round-4 requirement: search should surface authoritative,
    # high-confidence work). 0 when the provider did not report a count --
    # an absent number is honest, never assumed.
    citation_count: int = 0
