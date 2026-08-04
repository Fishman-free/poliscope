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
from packages.tools.adapters import SEARCH_ADAPTER_NAMES, adapter, search_adapter
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
    doi: str | None
    title: str
    evidence_level: str
    requesting_seats: frozenset[Seat]
    already_known: bool = False
    # Feeds packages.evidence.source_diversity.check_diversity in the
    # acquisition round. Populated from the persisted row either way, so a
    # cache hit and a fresh fetch report the same fields.
    authors: tuple[str, ...] = ()
    dataset_id: str | None = None
    # Only set by acquire_uploaded -- lets run_acquisition hand the right
    # object id to FindingExtractor.extract_uploaded without guessing it back
    # from position in the result list.
    object_id: UUID | None = None


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

    async def _existing_by_object(self, object_id: UUID) -> SourceModel | None:
        """Dedup lookup for uploaded PDFs, parallel to ``_existing``'s DOI one.

        An uploaded file has no DOI to key on, so the object id -- already
        unique per stored PDF -- plays that role instead.
        """
        row: SourceModel | None = await self._session.scalar(
            select(SourceModel).where(
                SourceModel.task_id == self._task_id,
                SourceModel.object_id == object_id,
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
            authors=list(normalized.authors),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _persist_uploaded(self, object_id: UUID) -> SourceModel:
        """Persist a source that came from an upload, not a lookup.

        There is no ``NormalizedSource`` to draw a title or authors from --
        the file is bytes on disk until ``FindingExtractor.extract_uploaded``
        actually reads it. Title is left empty rather than guessed at, per
        CLAUDE.md 7: an unknown must stay visibly unknown, not be papered over
        with a placeholder that looks like real metadata.
        """
        row = SourceModel(
            id=uuid4(),
            task_id=self._task_id,
            doi=None,
            canonical_doi=None,
            title="",
            provider_ids={},
            authors=[],
            object_id=object_id,
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
        free_text: dict[str, set[Seat]] = {}
        for query in planned:
            for seat in sorted(query.requesting_seats, key=lambda item: item.value):
                candidate = await pool.add(seat, query.query)
                if candidate.normalized_doi is None:
                    # No DOI in the raw query -- e.g. an adversarial-retrieval
                    # intent string. Tried below via real free-text search
                    # instead of being marked unresolvable on sight.
                    free_text.setdefault(query.query, set()).add(seat)
                    continue
                seats_by_doi.setdefault(candidate.normalized_doi, set()).add(seat)

        acquired: list[AcquiredSource] = []
        refused: list[RefusedCandidate] = []
        unresolvable: list[str] = []
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
                        authors=tuple(existing.authors),
                        dataset_id=existing.dataset_id,
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
                    authors=tuple(normalized.authors),
                    # No adapter resolves a dataset identifier from a DOI
                    # lookup alone (NormalizedSource has no such field), so
                    # this is always None at acquisition time. It can still
                    # end up populated later: packages.papers.finding_extraction
                    # scans the full text this row's DOI eventually resolves
                    # to and writes a real value back onto this same
                    # SourceModel row when a Data Availability declaration is
                    # found -- reports/workspace queries re-read the row from
                    # the database rather than reusing this dataclass, so
                    # that later write is what they actually see.
                    dataset_id=row.dataset_id,
                )
            )

        for text in sorted(free_text):
            seats = frozenset(free_text[text])
            # Budget is spent before the search rather than after, mirroring
            # the DOI path -- but here it must happen before we know whether
            # any provider will hit, since the DOI (and therefore whether
            # this is already a known source) is unknown until a provider
            # answers. A query that turns out already-known still cost one
            # spend; that mirrors a real vendor call having been attempted.
            if not self._spend():
                refused.append(RefusedCandidate(text, "source budget exhausted"))
                continue
            resolved = await self._search_first_hit(text)
            if resolved is None:
                # Every free, keyless provider missed (or errored). Recorded
                # honestly rather than faked as a hit -- CLAUDE.md 7.
                unresolvable.append(text)
                continue
            doi = normalize_doi(resolved.doi)
            existing = await self._existing(doi)
            if existing is not None:
                acquired.append(
                    AcquiredSource(
                        source_id=existing.id,
                        doi=doi,
                        title=existing.title,
                        evidence_level=METADATA_EVIDENCE_LEVEL,
                        requesting_seats=seats,
                        already_known=True,
                        authors=tuple(existing.authors),
                        dataset_id=existing.dataset_id,
                    )
                )
                continue
            if resolved.retracted:
                refused.append(RefusedCandidate(text, "source is retracted"))
                continue
            row = await self._persist(resolved)
            acquired.append(
                AcquiredSource(
                    source_id=row.id,
                    doi=doi,
                    title=resolved.title,
                    evidence_level=METADATA_EVIDENCE_LEVEL,
                    requesting_seats=seats,
                    authors=tuple(resolved.authors),
                    dataset_id=row.dataset_id,
                )
            )

        return AcquisitionResult(
            planned_queries=len(planned),
            acquired=tuple(acquired),
            refused=tuple(refused),
            unresolvable=tuple(dict.fromkeys(unresolvable)),
        )

    async def acquire_uploaded(
        self,
        object_ids: tuple[UUID, ...],
    ) -> AcquisitionResult:
        """Turn already-stored uploads into ``sources`` rows.

        Unlike ``acquire``, there is no discovery step: the file is already on
        disk, so nothing is planned, searched, or fetched from a provider.
        Only persistence and dedup by object id -- the parallel of ``acquire``'s
        dedup by DOI, since an upload has no DOI to key on.
        """
        acquired: list[AcquiredSource] = []
        for object_id in object_ids:
            existing = await self._existing_by_object(object_id)
            if existing is not None:
                # Same file uploaded again, or a second seat referencing the
                # same object id -- one row, same as a repeated DOI.
                acquired.append(
                    AcquiredSource(
                        source_id=existing.id,
                        doi=None,
                        title=existing.title,
                        evidence_level=METADATA_EVIDENCE_LEVEL,
                        requesting_seats=frozenset(),
                        already_known=True,
                        authors=tuple(existing.authors),
                        dataset_id=existing.dataset_id,
                        object_id=object_id,
                    )
                )
                continue
            row = await self._persist_uploaded(object_id)
            acquired.append(
                AcquiredSource(
                    source_id=row.id,
                    doi=None,
                    title=row.title,
                    evidence_level=METADATA_EVIDENCE_LEVEL,
                    requesting_seats=frozenset(),
                    authors=tuple(row.authors),
                    dataset_id=row.dataset_id,
                    object_id=object_id,
                )
            )
        return AcquisitionResult(
            planned_queries=0,
            acquired=tuple(acquired),
        )

    async def _search_first_hit(self, query: str) -> NormalizedSource | None:
        """Try each free, keyless search provider in order; first hit wins.

        A provider miss (``None``) or a raised error (network failure, rate
        limit, malformed response) both fall through to the next provider
        rather than aborting the whole query -- CLAUDE.md 10 wants a single
        failure to degrade, not to blow up the pass for every other query.
        Only when every provider has missed or errored does the caller record
        the query as unresolvable.
        """
        for name in SEARCH_ADAPTER_NAMES:
            provider = search_adapter(name, self._gateway, self._task_id)
            try:
                hit = await provider.search(query)
            except Exception:
                continue
            if hit is not None:
                return hit
        return None

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
