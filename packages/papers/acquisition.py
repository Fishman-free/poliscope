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

import asyncio
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.council.contracts import Seat
from packages.council.rounds.registry import KnowledgeDocumentLike
from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.evidence.process_stream import ProcessCallback
from packages.papers.candidate_pool import CandidatePool
from packages.papers.models import SourceModel
from packages.papers.query_planner import QueryPlanner
from packages.papers.query_sanitize import sanitize_search_query
from packages.papers.relevance import (
    DEFAULT_RELEVANCE_THRESHOLD,
    is_topically_relevant,
    within_cutoff,
)
from packages.tools.adapters import SEARCH_ADAPTER_NAMES, adapter, search_adapter
from packages.tools.adapters.normalization import NormalizedSource, normalize_doi
from packages.tools.contracts import ToolGateway

# Metadata retrieved from a provider, with no full text read. CLAUDE.md 7.1.
METADATA_EVIDENCE_LEVEL = "B"

# The provider asked first. Others are configured but unused until a fallback
# policy exists; picking one silently would hide which provider a fact came from.
PRIMARY_ADAPTER = "openalex"

# Wall-clock ceilings for one acquisition pass (see SourceAcquisition.acquire):
# per-query and whole-pass totals so a rate-limited or half-open vendor stalls
# one pass for minutes, never the whole worker. Per-query work is also
# bounded-concurrency (ACQUISITION_CONCURRENCY) so several free-text queries
# fetch in parallel instead of serially stretching into a long silence.
ACQUISITION_PER_QUERY_SECONDS = 45.0
ACQUISITION_TOTAL_SECONDS = 600.0
ACQUISITION_CONCURRENCY = 3


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
    # Only set by acquire_knowledge_documents -- the knowledge-document id
    # this Source was built from, for FindingExtractor.extract_knowledge_document.
    document_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentRef:
    """One knowledge-base document the researcher linked to this task.

    Carries everything acquisition needs to persist a Source row for it
    (sources.knowledge_document_id) without importing packages.knowledge --
    the worker assembles these from the knowledge_documents table.
    """

    document_id: UUID
    object_key: str
    title: str


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
        *,
        on_process: ProcessCallback | None = None,
        relevance_context: tuple[str, ...] = (),
        relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
        relevance_enabled: bool = True,
        corpus_cutoff_year: int | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._task_id = task_id
        self._budget = budget
        # Live-view trace: every lookup/search the council performs becomes a
        # clickable row in the workbench, so the researcher sees *where the
        # papers came from*, not just that papers appeared (CLAUDE.md 7.4).
        self._on_process = on_process
        # B5 post-retrieval relevance filter + A3 corpus cutoff. Both apply
        # only to *discovered* sources (this acquisition pass), never to the
        # researcher's own uploaded/DOI/knowledge evidence, which is admitted
        # through the separate acquire_* methods and must not be silently
        # censored. Every excluded candidate becomes a RefusedCandidate with
        # its exact reason -- nothing disappears quietly (CLAUDE.md 7).
        self._relevance_context = tuple(relevance_context)
        self._relevance_threshold = relevance_threshold
        self._relevance_enabled = relevance_enabled
        self._corpus_cutoff_year = corpus_cutoff_year

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

    async def _existing_by_knowledge_document(
        self, document_id: UUID
    ) -> SourceModel | None:
        """Dedup lookup for knowledge-base documents, parallel to the other
        two ``_existing`` variants: a document has no DOI, so its id -- unique
        per stored document -- plays the keying role, and a replayed run or a
        document reused across two tasks never mints a second Source row."""
        row: SourceModel | None = await self._session.scalar(
            select(SourceModel).where(
                SourceModel.task_id == self._task_id,
                SourceModel.knowledge_document_id == document_id,
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
            publication_year=normalized.year,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    def _screen_discovered(
        self,
        normalized: NormalizedSource,
        query: str,
        *,
        relevance_screen: bool = True,
    ) -> str | None:
        """Return a refusal reason for a *discovered* candidate, or None to admit.

        Applies the A3 corpus cutoff first (a post-cutoff paper is outside the
        time-travel window), then the B5 deterministic relevance filter. A
        candidate with an unknown year is admitted on date grounds (the unknown
        stays visible), and an empty relevance context fails open.

        ``relevance_screen=False`` is used when a seat explicitly named the
        DOI: the B5 filter exists to drop off-topic hits from *free-text*
        search, never to second-guess a specific identifier a scientist asked
        for. The corpus cutoff still applies -- a time-travel replay cannot
        admit a post-cutoff paper just because a seat named its DOI.
        """
        if self._corpus_cutoff_year is not None and not within_cutoff(
            normalized.year, self._corpus_cutoff_year
        ):
            return (
                f"after corpus cutoff {self._corpus_cutoff_year} "
                f"(published {normalized.year})"
            )
        if relevance_screen and self._relevance_enabled:
            admitted, score = is_topically_relevant(
                self._relevance_context,
                normalized.title,
                threshold=self._relevance_threshold,
            )
            if not admitted:
                return f"below relevance threshold (score={score:.3f})"
        return None

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

        # Whole-pass ceiling: one acquisition pass must never hold the worker
        # hostage behind a rate-limited or half-open vendor. Checked between
        # items -- a natural cancellation point -- and the remainder is
        # reported honestly as a refused/timeout candidate (CLAUDE.md 10).
        deadline = time.monotonic() + ACQUISITION_TOTAL_SECONDS

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
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": doi,
                            "miss": True,
                            "reason": "source budget exhausted",
                        },
                    )
                continue
            if time.monotonic() >= deadline:
                refused.append(RefusedCandidate(doi, "acquisition timed out"))
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": doi,
                            "miss": True,
                            "reason": "acquisition timed out",
                        },
                    )
                continue
            if self._on_process is not None:
                self._on_process(
                    "tool_call",
                    {
                        "kind": "doi_lookup",
                        "query": doi,
                        "seats": sorted(seat.value for seat in seats),
                    },
                )
            try:
                normalized = await asyncio.wait_for(
                    source_adapter.lookup_doi(doi),
                    timeout=ACQUISITION_PER_QUERY_SECONDS,
                )
            except Exception as error:
                reason = (
                    "acquisition timed out"
                    if isinstance(error, TimeoutError)
                    else f"lookup failed: {error!r}"
                )
                refused.append(RefusedCandidate(doi, reason))
                # The live view pairs tool_call/tool_result into one card; a
                # refused DOI used to send no tool_result at all, so the card
                # sat on "等待结果…" forever even though the query had already
                # failed. Every refused query now closes its card with a miss
                # and the honest reason (CLAUDE.md 10).
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {"query": doi, "miss": True, "reason": reason},
                    )
                continue
            if normalized.retracted:
                # A retracted paper is refused here as well as at the gate. The
                # gate is the guarantee; this keeps it out of `sources`, where it
                # would otherwise inflate the paper count.
                refused.append(RefusedCandidate(doi, "source is retracted"))
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": doi,
                            "miss": True,
                            "reason": "source is retracted",
                        },
                    )
                continue
            # Explicit DOI lookup: corpus cutoff still applies, but the B5
            # topical filter does not -- a named identifier is a directed fetch.
            screen_reason = self._screen_discovered(
                normalized, doi, relevance_screen=False
            )
            if screen_reason is not None:
                refused.append(RefusedCandidate(doi, screen_reason))
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": doi,
                            "miss": True,
                            "reason": screen_reason,
                        },
                    )
                continue
            if self._on_process is not None:
                self._on_process(
                    "tool_result",
                    {
                        "query": doi,
                        "doi": normalized.doi or doi,
                        "title": normalized.title,
                        "url": f"https://doi.org/{doi}",
                        "citation_count": normalized.citation_count,
                    },
                )
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

        # Free-text pass: planning (budget + tool_call event) runs first so the
        # live view shows every query starting in order; the provider fetches
        # then run bounded-concurrency (ACQUISITION_CONCURRENCY) with a per-query
        # hard ceiling, so a handful of adversarial-retrieval intents finish in
        # one parallel burst instead of serially stretching the pass into a
        # minutes-long silence. A timed-out query is a reported miss, not a hang.
        sem = asyncio.Semaphore(ACQUISITION_CONCURRENCY)

        async def _fetch(
            text: str,
        ) -> tuple[str, NormalizedSource | None, bool]:
            async with sem:
                try:
                    hit = await asyncio.wait_for(
                        self._search_first_hit(text),
                        timeout=ACQUISITION_PER_QUERY_SECONDS,
                    )
                    return text, hit, False
                except TimeoutError:
                    return text, None, True

        planned_texts: list[str] = []
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
            if time.monotonic() >= deadline:
                refused.append(RefusedCandidate(text, "acquisition timed out"))
                continue
            if self._on_process is not None:
                self._on_process(
                    "tool_call",
                    {
                        "kind": "search",
                        "query": text,
                        "seats": sorted(seat.value for seat in seats),
                    },
                )
            planned_texts.append(text)

        fetch_results = dict(
            (text, (hit, timed_out))
            for text, hit, timed_out in await asyncio.gather(
                *(_fetch(text) for text in planned_texts)
            )
        )

        for text in planned_texts:
            seats = frozenset(free_text[text])
            resolved, timed_out = fetch_results[text]
            if timed_out:
                refused.append(RefusedCandidate(text, "acquisition timed out"))
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": text,
                            "miss": True,
                            "reason": "acquisition timed out",
                        },
                    )
                continue
            if resolved is None:
                # Every free, keyless provider missed (or errored). Recorded
                # honestly rather than faked as a hit -- CLAUDE.md 7.
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": text,
                            "miss": True,
                            "reason": "no provider returned a result",
                        },
                    )
                unresolvable.append(text)
                continue
            if self._on_process is not None:
                self._on_process(
                    "tool_result",
                    {
                        "query": text,
                        "doi": resolved.doi,
                        "title": resolved.title,
                        "url": f"https://doi.org/{resolved.doi}",
                        "citation_count": resolved.citation_count,
                    },
                )
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
            screen_reason = self._screen_discovered(resolved, text)
            if screen_reason is not None:
                refused.append(RefusedCandidate(text, screen_reason))
                if self._on_process is not None:
                    self._on_process(
                        "tool_result",
                        {
                            "query": text,
                            "miss": True,
                            "reason": screen_reason,
                        },
                    )
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

    async def acquire_dois(
        self,
        dois: tuple[str, ...],
    ) -> AcquisitionResult:
        """Resolve the researcher's own DOIs into persisted sources.

        Mirror of ``acquire``'s DOI path minus the planning step: these DOIs
        were not asked for by any seat (``requesting_seats`` stays empty),
        they are handed to acquisition already identified, and they
        deduplicate against the same canonical-DOI ``_existing`` lookup, so a
        DOI the researcher listed that a seat also requested costs one fetch.
        Retracted papers are refused here for the same reason as in
        ``acquire`` -- keep them out of ``sources`` so they cannot inflate
        the paper count before the gate ever sees them.
        """
        acquired: list[AcquiredSource] = []
        refused: list[RefusedCandidate] = []
        source_adapter = adapter(PRIMARY_ADAPTER, self._gateway, self._task_id)
        for doi in sorted(normalize_doi(item) for item in dois):
            existing = await self._existing(doi)
            if existing is not None:
                acquired.append(
                    AcquiredSource(
                        source_id=existing.id,
                        doi=doi,
                        title=existing.title,
                        evidence_level=METADATA_EVIDENCE_LEVEL,
                        requesting_seats=frozenset(),
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
                refused.append(RefusedCandidate(doi, "source is retracted"))
                continue
            row = await self._persist(normalized)
            acquired.append(
                AcquiredSource(
                    source_id=row.id,
                    doi=doi,
                    title=normalized.title,
                    evidence_level=METADATA_EVIDENCE_LEVEL,
                    requesting_seats=frozenset(),
                    authors=tuple(normalized.authors),
                    dataset_id=row.dataset_id,
                )
            )
        return AcquisitionResult(
            planned_queries=len(dois),
            acquired=tuple(acquired),
            refused=tuple(refused),
        )

    async def acquire_knowledge_documents(
        self,
        documents: tuple[KnowledgeDocumentLike, ...],
    ) -> AcquisitionResult:
        """Turn linked knowledge-base documents into ``sources`` rows.

        Same shape as ``acquire_uploaded`` -- no discovery step, persistence
        plus dedup only -- keyed by ``sources.knowledge_document_id`` so a
        document the researcher linked to two tasks yields one Source per
        task (sources rows are task-scoped) and a replayed run reuses the
        row. The title comes from the document's stored filename; nothing is
        guessed about the paper itself (CLAUDE.md 7).
        """
        acquired: list[AcquiredSource] = []
        for document in documents:
            existing = await self._existing_by_knowledge_document(
                document.document_id
            )
            if existing is not None:
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
                        document_id=document.document_id,
                    )
                )
                continue
            row = SourceModel(
                id=uuid4(),
                task_id=self._task_id,
                doi=None,
                canonical_doi=None,
                title=document.title,
                provider_ids={},
                authors=[],
                knowledge_document_id=document.document_id,
            )
            self._session.add(row)
            await self._session.flush()
            acquired.append(
                AcquiredSource(
                    source_id=row.id,
                    doi=None,
                    title=row.title,
                    evidence_level=METADATA_EVIDENCE_LEVEL,
                    requesting_seats=frozenset(),
                    authors=tuple(row.authors),
                    dataset_id=row.dataset_id,
                    document_id=document.document_id,
                )
            )
        return AcquisitionResult(
            planned_queries=0,
            acquired=tuple(acquired),
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
        cleaned = sanitize_search_query(query)
        if cleaned is None:
            return None
        for name in SEARCH_ADAPTER_NAMES:
            provider = search_adapter(name, self._gateway, self._task_id)
            try:
                hit = await provider.search(cleaned)
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
    "KnowledgeDocumentRef",
    "RefusedCandidate",
    "SourceAcquisition",
]
