from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.council.contracts import Seat
from packages.tools.adapters.normalization import normalize_doi

# A DOI is a specific shape -- "10.<4-9 digits>/<DOI chars>" -- and seats often
# paste one in front of a longer justification sentence ("doi:10.xxxx/yyyy：
# 核对其中是否包含..."). Extracting with a regex instead of a whitespace split
# keeps the DOI and only the DOI: the old split()[0] grabbed the whole
# whitespace-free Chinese tail, and the entire sentence was then sent to the
# provider as a DOI, producing a 404 like
#   https://api.openalex.org/works/doi:10.1001/...%EF%BC%9A%E6%A0%B8%E6%9F%A5...
# DOI characters are [0-9A-Za-z._;()/:+-]; anything else (full-width colon,
# Chinese text) terminates the match, so the suffix sentence is dropped.
_DOI_RE = re.compile(r"10\.\d{4,9}/[0-9A-Za-z._;()/:+-]+")


@dataclass(frozen=True, slots=True)
class CandidateRequest:
    seat: Seat
    raw_query: str
    normalized_doi: str | None = None


@dataclass
class CandidatePool:
    _requests: dict[str, CandidateRequest] = field(default_factory=dict)
    _seats_per_doi: dict[str, set[Seat]] = field(default_factory=dict)

    async def add(self, seat: Seat, raw_query: str) -> CandidateRequest:
        normalized = None
        match = _DOI_RE.search(raw_query)
        if match is not None:
            normalized = normalize_doi(match.group(0))
        req = CandidateRequest(
            seat=seat, raw_query=raw_query, normalized_doi=normalized
        )
        self._requests[str(id(req))] = req
        if normalized:
            self._seats_per_doi.setdefault(normalized, set()).add(seat)
        return req

    async def by_doi(self, doi: str) -> frozenset[Seat]:
        return frozenset(self._seats_per_doi.get(normalize_doi(doi), set()))

    async def count(self) -> int:
        return len(self._requests)
