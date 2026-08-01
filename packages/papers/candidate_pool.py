from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.tools.adapters.normalization import normalize_doi


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
        if "10." in raw_query:
            normalized = normalize_doi(raw_query.split("10.")[-1].split()[0] if "10." in raw_query else raw_query)
            normalized = "10." + normalized if not normalized.startswith("10.") else normalized
        req = CandidateRequest(seat=seat, raw_query=raw_query, normalized_doi=normalized)
        self._requests[str(id(req))] = req
        if normalized:
            self._seats_per_doi.setdefault(normalized, set()).add(seat)
        return req

    async def by_doi(self, doi: str) -> frozenset[Seat]:
        return frozenset(self._seats_per_doi.get(normalize_doi(doi), set()))

    async def count(self) -> int:
        return len(self._requests)
