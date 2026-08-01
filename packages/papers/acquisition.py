"""Turns the seats' evidence requests into persisted, levelled sources.

The discovery pieces all existed -- a query planner, a candidate pool, four
provider adapters, a normalisation model -- and nothing joined them, so no Source
ever reached the database and the whole A-D evidence ladder was exercised only
by tests.

Two rules govern what comes out:

* **One fetch per paper, however many seats asked.** CLAUDE.md 3 gives the seven
  seats a shared tool cache. The planner merges identical queries and the pool
  keys candidates by normalised DOI, so a paper four seats want costs one call.

* **Metadata is Level B, never Level A.** CLAUDE.md 7.1 reserves Level A for
  sources whose full text and exact wording are available. An adapter returns
  bibliographic metadata, so that is what is claimed. Promoting it would let a
  high-confidence causal conclusion rest on an abstract, which 7.1 forbids
  outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.council.contracts import Seat
from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.papers.candidate_pool import CandidatePool
from packages.papers.models import SourceModel
from packages.papers.query_planner import QueryPlanner
from packages.tools.adapters import adapter
from packages.tools.adapters.normalization import NormalizedSource, normalize_doi
from packages.tools.contracts import ToolGateway

# Metadata retrieved from a provider, with no full text read. CLAUDE.md 7.1.
METADATA_EVIDENCE_LEVEL = "B"

# The provider asked first. Others are configured but unused until a fallback
# policy exists; picking one silently would hide which provider a fact came from.
PRIMARY_ADAPTER = "openalex"


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    """One source that reached the database, with what is known about it."""

    source_id: UUID
    doi: str
    title: str
    evidence_level: str
    requesting_seats: frozenset[Seat]
    already_known: bool = False


@dataclass(frozen=True, slots=True)
class RefusedCandidate:
    """A candidate that was deliberately not admitted, and why.

    Refusals are returned rather than dropped: a request that quietly yielded
    nothing is indistinguishable from one that was never made, and CLAUDE.md 7
    requires the unknown to be visible.
    """

    query: str
    reason: str


@dataclass
class AcquisitionResult:
    planned_queries: int = 0
    acquired: tuple[AcquiredSource, ...] = ()
    refused: tuple[RefusedCandidate, ...] = ()
    unresolvable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tool_calls(self) -> int:
        return sum(1 for item in self.acquired if not item.already_known)


class SourceAcquisition:
    """Resolves evidence requests into ``sources`` rows for one task."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: ToolGateway,
        task_id: UUID,
        budget: BudgetTracker | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._task_id = task_id
        self._budget = budget

    async def _existing(self, doi: str) -> SourceModel | None:
        row: SourceModel | None = await self._session.scalar(
            select(SourceModel).where(
                SourceModel.task_id == self._task_id,
                SourceModel.canonical_doi == doi,
            )
        )
        return row

    async def _persist(self, normalized: NormalizedSource) -> SourceModel:
        row = SourceModel(
            id=uuid4(),
            task_id=self._task_id,
            doi=normalized.doi,
            canonical_doi=normalize_doi(normalized.doi),
            title=normalized.title,
            provider_ids=dict(normalized.provider_ids),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def acquire(
        self,
        requests: list[tuple[Seat, str]],
    ) -> AcquisitionResult:
        """Plan, deduplicate, fetch, and persist. Never raises on one bad query."""
        planner = QueryPlanner()
        planned = planner.merge_requests(requests)
        pool = CandidatePool()

        seats_by_doi: dict[str, set[Seat]] = {}
        unresolvable: list[str] = []
        for query in planned:
            for seat in sorted(query.requesting_seats, key=lambda item: item.value):
                candidate = await pool.add(seat, query.query)
                if candidate.normalized_doi is None:
                    # Free-text search needs a search operation the adapters do
                    # not expose yet. Recorded, not silently dropped.
                    unresolvable.append(query.query)
                    continue
                seats_by_doi.setdefault(candidate.normalized_doi, set()).add(seat)

        acquired: list[AcquiredSource] = []
        refused: list[RefusedCandidate] = []
        source_adapter = adapter(PRIMARY_ADAPTER, self._gateway, self._task_id)

        for doi in sorted(seats_by_doi):
            seats = frozenset(seats_by_doi[doi])
            existing = await self._existing(doi)
            if existing is not None:
                # One fetch per paper: the second seat to ask gets the cached row.
                acquired.append(
                    AcquiredSource(
                        source_id=existing.id,
                        doi=doi,
                        title=existing.title,
                        evidence_level=METADATA_EVIDENCE_LEVEL,
                        requesting_seats=seats,
                        already_known=True,
                    )
                )
                continue
            if not self._spend():
                refused.append(RefusedCandidate(doi, "source budget exhausted"))
                continue
            try:
                normalized = await source_adapter.lookup_doi(doi)
            except Exception as error:
                refused.append(RefusedCandidate(doi, f"lookup failed: {error!r}"))
                continue
            if normalized.retracted:
                # A retracted paper is refused here as well as at the gate. The
                # gate is the guarantee; this keeps it out of `sources`, where it
                # would otherwise inflate the paper count.
                refused.append(RefusedCandidate(doi, "source is retracted"))
                continue
            row = await self._persist(normalized)
            acquired.append(
                AcquiredSource(
                    source_id=row.id,
                    doi=doi,
                    title=normalized.title,
                    evidence_level=METADATA_EVIDENCE_LEVEL,
                    requesting_seats=seats,
                )
            )

        return AcquisitionResult(
            planned_queries=len(planned),
            acquired=tuple(acquired),
            refused=tuple(refused),
            unresolvable=tuple(dict.fromkeys(unresolvable)),
        )

    def _spend(self) -> bool:
        """Charge one source and one tool call, reporting exhaustion as False.

        CLAUDE.md 10 wants an exhausted budget to produce a reported gap, so this
        returns rather than raises and the caller records a refusal.
        """
        if self._budget is None:
            return True
        try:
            self._budget.consume_source()
            self._budget.consume_tool_call()
        except BudgetExhausted:
            return False
        return True


__all__ = [
    "METADATA_EVIDENCE_LEVEL",
    "PRIMARY_ADAPTER",
    "AcquiredSource",
    "AcquisitionResult",
    "RefusedCandidate",
    "SourceAcquisition",
]
