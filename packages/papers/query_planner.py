from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    query_id: UUID
    source: str
    query: str
    requesting_seats: frozenset[Seat]


@dataclass
class QueryPlanner:
    _queries: list[PlannedQuery] = field(default_factory=list)

    def merge_requests(self, requests: list[tuple[Seat, str]]) -> list[PlannedQuery]:
        by_query: dict[str, set[Seat]] = {}
        for seat, query in requests:
            by_query.setdefault(query, set()).add(seat)
        return [
            PlannedQuery(
                query_id=uuid4(),
                source="openalex",
                query=query,
                requesting_seats=frozenset(seats),
            )
            for query, seats in by_query.items()
        ]
