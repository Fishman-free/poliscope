"""Evidence acquisition, end to end: request -> tool call -> Source -> graph.

Until this path worked, no Source ever reached the database, which meant the A-D
evidence ladder, the paper-versus-cluster count, and every StudyFinding anchor
were structurally unreachable outside of tests.

The properties asserted are the ones that keep the ladder honest: metadata is
Level B and never Level A (CLAUDE.md 7.1), a retracted paper is refused
(CLAUDE.md 7.3), one paper costs one fetch however many seats asked
(CLAUDE.md 3), and every refusal is reported rather than dropped (CLAUDE.md 10).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.council.rounds.registry import SOURCE_REFUSED
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.papers.acquisition import METADATA_EVIDENCE_LEVEL
from packages.papers.models import ObjectModel, SourceModel
from packages.papers.object_store import PrivateObjectStore
from packages.research.models import ResearchTaskModel
from packages.tools.contracts import ToolRequest, ToolResult

QUESTION = "Does adolescent social media use cause depressive symptoms?"
GOOD_DOI = "10.1234/good-cohort-study"
RETRACTED_DOI = "10.1234/retracted-study"


class _RequestingGateway:
    """A model gateway whose seats all ask for the same two DOIs."""

    def __init__(self, dois: tuple[str, ...]) -> None:
        self._dois = dois

    async def invoke(self, request: ModelRequest) -> ModelResult:
        payload: dict[str, object] = {}
        if TaskPhase(request.purpose) is TaskPhase.ACQUISITION:
            payload = {"requests": [f"doi {doi}" for doi in self._dois]}
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


class _ProviderGateway:
    """A tool gateway standing in for OpenAlex, counting its own calls."""

    def __init__(self) -> None:
        self.calls: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        doi = str(request.arguments["doi"])
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "id": f"https://openalex.org/{doi}",
                    "title": f"Study of {doi}",
                    "authors": ("A. Researcher",),
                    "year": 2021,
                    "type": "journal-article",
                    "retracted": doi == RETRACTED_DOI,
                }
            ),
            latency_ms=3,
            retries=0,
            error_code=None,
        )


FREE_TEXT_ADVERSARIAL_QUERY = (
    "reverse causation candidate for screen time and depression"
)
UNRESOLVABLE_FREE_TEXT_QUERY = "a claim no free provider has ever indexed"
SEARCH_HIT_DOI = "10.9999/counterexample"


class _FreeTextRequestingGateway:
    """A model gateway whose seats ask a free-text (non-DOI) query.

    Mirrors an adversarial-retrieval intent string
    (``packages.evidence.adversarial_retrieval``): no DOI-shaped substring, so
    ``CandidatePool.add`` cannot resolve it and ``SourceAcquisition`` must
    fall back to real search.
    """

    def __init__(self, query: str) -> None:
        self._query = query

    async def invoke(self, request: ModelRequest) -> ModelResult:
        payload: dict[str, object] = {}
        if TaskPhase(request.purpose) is TaskPhase.ACQUISITION:
            payload = {"requests": [self._query]}
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


class _SearchProviderGateway:
    """A tool gateway resolving both DOI lookups and free-text search.

    Only ``openalex`` -- the first provider ``SourceAcquisition`` tries
    (``SEARCH_ADAPTER_NAMES``) -- ever returns a search hit; crossref and
    semantic_scholar always miss. That lets a test tell "resolved on the
    first provider tried" apart from "every provider missed" without having
    to script all three identically.
    """

    def __init__(self, hit_doi: str | None) -> None:
        self._hit_doi = hit_doi
        self.calls: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        if request.operation == "lookup_doi":
            doi = str(request.arguments["doi"])
            return ToolResult(
                call_id=uuid4(),
                payload=FrozenDict(
                    {
                        "id": f"https://openalex.org/{doi}",
                        "title": f"Study of {doi}",
                        "authors": ("A. Researcher",),
                        "year": 2021,
                        "type": "journal-article",
                        "retracted": False,
                    }
                ),
                latency_ms=3,
                retries=0,
                error_code=None,
            )
        # operation == "search": only openalex ever hits, and only when this
        # gateway was configured with a DOI to hit on.
        if request.tool_name != "openalex" or self._hit_doi is None:
            return ToolResult(
                call_id=uuid4(),
                payload=FrozenDict({"doi": None}),
                latency_ms=3,
                retries=0,
                error_code=None,
            )
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "doi": self._hit_doi,
                    "id": f"https://openalex.org/{self._hit_doi}",
                    "title": "A counterexample study",
                    "authors": ("Rivera",),
                    "year": 2021,
                    "type": "journal-article",
                    "retracted": False,
                }
            ),
            latency_ms=3,
            retries=0,
            error_code=None,
        )


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
    source_limit: int = 50,
) -> UUID:
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="acquisition_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=source_limit,
                user_evidence={},
            )
        )
        await session.commit()
    return task_id


async def _seed_uploaded(
    sessions: async_sessionmaker[AsyncSession],
    object_ids: tuple[UUID, ...],
) -> UUID:
    """Like ``_seed``, but the task's ``user_evidence`` already names uploaded
    object ids -- the shape ``apps/api/routers/papers.py`` produces, minus its
    own dedup-on-append (a duplicate here is deliberate: it lets a test drive
    ``SourceAcquisition.acquire_uploaded``'s own dedup path within a single
    acquisition round, rather than only across separate runs).
    """
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="acquisition_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={"pdf_object_ids": [str(oid) for oid in object_ids]},
            )
        )
        await session.commit()
    return task_id


async def _sources(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[SourceModel]:
    async with sessions() as session:
        result = await session.execute(
            select(SourceModel).where(SourceModel.task_id == task_id)
        )
        return list(result.scalars())


async def _source_nodes(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[GraphNodeModel]:
    async with sessions() as session:
        result = await session.execute(
            select(GraphNodeModel).where(
                GraphNodeModel.task_id == task_id,
                GraphNodeModel.node_type == EvidenceNodeType.SOURCE.value,
            )
        )
        return list(result.scalars())


async def _events_of_type(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    event_type: str,
) -> list[ScientificEventModel]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.event_type == event_type,
            )
        )
        return list(result.scalars())


async def test_a_requested_paper_becomes_a_persisted_source(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id = await _seed(app_sessions)
    tools = _ProviderGateway()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI,)),
        tools=tools,
    )

    sources = await _sources(app_sessions, task_id)
    assert [source.canonical_doi for source in sources] == [GOOD_DOI]
    assert str(sources[0].provider_ids["openalex"]).endswith(GOOD_DOI)
    # Authors returned by the provider are persisted, not discarded -- they
    # feed SAME_RESEARCH_TEAM lineage detection (CLAUDE.md 7.4).
    assert sources[0].authors == ["A. Researcher"]


async def test_one_paper_costs_one_fetch_however_many_seats_asked(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 3 gives the seven seats a shared tool cache, not seven caches."""
    task_id = await _seed(app_sessions)
    tools = _ProviderGateway()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI,)),
        tools=tools,
    )

    # One OpenAlex metadata lookup plus one Unpaywall open-access lookup for the
    # freshly acquired source's finding-extraction attempt -- both made exactly
    # once per paper, never once per seat.
    assert len(tools.calls) == 2
    assert len(await _sources(app_sessions, task_id)) == 1


async def test_metadata_only_is_admitted_as_level_b_not_level_a(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 7.1 reserves Level A for retrievable full text and exact wording.

    An adapter returns bibliographic metadata. Claiming Level A for it would let
    a high-confidence causal conclusion rest on an abstract.
    """
    task_id = await _seed(app_sessions)

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI,)),
        tools=_ProviderGateway(),
    )

    events = await _events_of_type(
        app_sessions, task_id, EvidenceNodeType.SOURCE.value
    )
    assert [event.evidence_level for event in events] == [METADATA_EVIDENCE_LEVEL]
    # SOURCE_ONLY, so the node exists but is marked provisional rather than active.
    nodes = await _source_nodes(app_sessions, task_id)
    assert [node.status for node in nodes] == ["provisional"]


async def test_a_retracted_paper_is_refused_and_the_refusal_is_recorded(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 7.3 checks source authenticity; a silent skip hides the reason."""
    task_id = await _seed(app_sessions)

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI, RETRACTED_DOI)),
        tools=_ProviderGateway(),
    )

    stored = {source.canonical_doi for source in await _sources(app_sessions, task_id)}
    assert stored == {GOOD_DOI}
    refusals = await _events_of_type(app_sessions, task_id, SOURCE_REFUSED)
    assert [event.payload["query"] for event in refusals] == [RETRACTED_DOI]
    assert any(
        slot == f"ACQUISITION:refused:{RETRACTED_DOI}"
        for slot in result.run.unfilled_slots
    )


async def test_an_exhausted_source_budget_refuses_rather_than_overspends(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: report the unfilled slot, do not quietly exceed the budget."""
    task_id = await _seed(app_sessions, source_limit=1)
    tools = _ProviderGateway()

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI, RETRACTED_DOI)),
        tools=tools,
    )

    # One OpenAlex lookup for the sole source the budget allows, plus one
    # Unpaywall lookup for that source's finding-extraction attempt. The
    # budget-refused DOI never reaches either tool call.
    assert len(tools.calls) == 2
    assert any(
        "budget" in slot or "refused" in slot for slot in result.run.unfilled_slots
    )
    refusals = await _events_of_type(app_sessions, task_id, SOURCE_REFUSED)
    assert any("budget" in str(event.payload["reason"]) for event in refusals)


async def test_no_tool_provider_records_the_gap_instead_of_inventing_sources(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The requests are still recorded; the sources are not fabricated."""
    task_id = await _seed(app_sessions)

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((GOOD_DOI,)),
    )

    assert await _sources(app_sessions, task_id) == []
    assert "ACQUISITION:no_tool_provider" in result.run.unfilled_slots
    requested = await _events_of_type(app_sessions, task_id, "EVIDENCE_REQUESTED")
    assert {str(event.payload["seat"]) for event in requested} == {
        seat.value for seat in Seat
    }


async def test_a_freetext_query_resolves_via_real_search(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A non-DOI intent string -- an adversarial-retrieval query's shape
    (CLAUDE.md 7.9 / packages.evidence.adversarial_retrieval) -- is tried
    against a real search-capable adapter and, on a hit, actually reaches the
    database, rather than being marked unresolvable on sight."""
    task_id = await _seed(app_sessions)
    tools = _SearchProviderGateway(hit_doi=SEARCH_HIT_DOI)

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_FreeTextRequestingGateway(FREE_TEXT_ADVERSARIAL_QUERY),
        tools=tools,
    )

    sources = await _sources(app_sessions, task_id)
    assert [source.canonical_doi for source in sources] == [SEARCH_HIT_DOI]
    search_calls = [call for call in tools.calls if call.operation == "search"]
    assert len(search_calls) == 1
    assert search_calls[0].tool_name == "openalex"
    assert search_calls[0].arguments["query"] == FREE_TEXT_ADVERSARIAL_QUERY


async def test_a_freetext_query_every_provider_missing_stays_unresolvable(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Every free, keyless provider missing is recorded honestly, not faked
    as a hit (CLAUDE.md 7)."""
    task_id = await _seed(app_sessions)
    tools = _SearchProviderGateway(hit_doi=None)

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_FreeTextRequestingGateway(UNRESOLVABLE_FREE_TEXT_QUERY),
        tools=tools,
    )

    assert await _sources(app_sessions, task_id) == []
    search_calls = [call for call in tools.calls if call.operation == "search"]
    assert {call.tool_name for call in search_calls} == {
        "openalex",
        "crossref",
        "semantic_scholar",
    }
    assert any(
        slot == f"ACQUISITION:unresolved:{UNRESOLVABLE_FREE_TEXT_QUERY}"
        for slot in result.run.unfilled_slots
    )


UPLOAD_QUOTE = "Uploaded adolescents report higher anxiety with more screen time."


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return bytes(document.tobytes())


def _upload_finding_payload(quote: str) -> dict[str, object]:
    return {
        "study_question": "Does screen time affect wellbeing?",
        "population": "adolescents",
        "design": "cross_sectional",
        "exposure_variable": "screen_time",
        "outcome_variable": "anxiety",
        "analysis_method": "linear regression",
        "finding_statement": "Uploaded study finds screen time correlates with anxiety",
        "origin": "SOURCE_TEXT",
        "effect_direction": "positive",
        "exact_quote": quote,
        "author_conclusions": ["Screen time matters."],
        "author_limitations": ["Self-reported."],
        "data_availability": "restricted",
        "code_availability": "unavailable",
        "preregistration": "not_reported",
        "method_quality": {
            "directness": 0.8,
            "design_quality": 0.75,
            "measurement_quality": 0.7,
            "precision": 0.65,
            "replicability": 0.6,
            "external_validity": 0.55,
        },
    }


class _UploadExtractionGateway:
    """Answers only the finding-extraction model call with a valid payload.

    Every phase-deliberation call (``request.purpose`` is a ``TaskPhase``
    value, e.g. ``"acquisition"``) gets an empty payload -- the uploaded-PDF
    branch does not depend on any seat requesting anything, unlike the DOI
    branch these other gateways in this file drive. Keeping the two purposes
    on separate branches (rather than reusing ``_RequestingGateway``, which
    calls ``TaskPhase(request.purpose)`` unconditionally) avoids a ``ValueError``
    on the literal string ``"finding_extraction"``, which is not a ``TaskPhase``
    member.
    """

    def __init__(self, quote: str) -> None:
        self._quote = quote

    async def invoke(self, request: ModelRequest) -> ModelResult:
        payload: dict[str, object] = {}
        if request.purpose == "finding_extraction":
            payload = _upload_finding_payload(self._quote)
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def test_uploading_the_same_object_id_twice_in_one_round_is_deduplicated(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """``SourceAcquisition.acquire_uploaded`` dedups by object_id, not DOI --
    an uploaded PDF has none to dedup on (CLAUDE.md 7.4 asks acquisition to
    recognize an already-known source rather than persist it twice)."""
    object_id = uuid4()
    task_id = await _seed_uploaded(app_sessions, (object_id, object_id))
    # sources.object_id carries a foreign key to objects.id -- the row must
    # exist even though this scenario never reads it back (no finding
    # extractor is configured, so _retrieve_uploaded is never reached).
    async with app_sessions() as session:
        session.add(
            ObjectModel(
                id=object_id,
                task_id=task_id,
                object_key=f"tasks/{task_id}/dedup-fixture.pdf",
                content_hash="0" * 64,
                encryption="AES256",
                content_type="application/pdf",
                size_bytes=10,
            )
        )
        await session.commit()
    tools = _ProviderGateway()

    await run_task(app_sessions, projector_sessions, task_id, tools=tools)

    sources = await _sources(app_sessions, task_id)
    assert len(sources) == 1
    assert sources[0].object_id == object_id
    assert sources[0].doi is None
    assert sources[0].canonical_doi is None
    source_events = await _events_of_type(
        app_sessions, task_id, EvidenceNodeType.SOURCE.value
    )
    assert len(source_events) == 1
    # Neither acquire_uploaded nor its dedup path ever calls a tool -- there is
    # no discovery step for an upload, only a database lookup.
    assert tools.calls == []


async def test_uploaded_pdf_produces_a_source_and_a_study_finding(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """The full upload -> parse -> StudyFinding chain (README known-gap #10):
    an uploaded PDF's own bytes, never an open-access fetch, become a Level A
    finding through the same ``_persist_finding`` path a DOI-acquired source
    uses (``packages/papers/finding_extraction.py``)."""
    task_id_placeholder = uuid4()  # object_key namespacing only; task seeded below
    store = PrivateObjectStore(root=str(tmp_path))
    stored = store.store(
        task_id=task_id_placeholder, content=_pdf_bytes(UPLOAD_QUOTE)
    )
    object_id = uuid4()
    task_id = await _seed_uploaded(app_sessions, (object_id,))

    async with app_sessions() as session:
        session.add(
            ObjectModel(
                id=object_id,
                task_id=task_id,
                object_key=stored.object_key,
                content_hash=stored.content_hash,
                encryption=stored.encryption,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
            )
        )
        await session.commit()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_UploadExtractionGateway(UPLOAD_QUOTE),
        tools=_ProviderGateway(),
        object_store=store,
    )

    sources = await _sources(app_sessions, task_id)
    assert [source.object_id for source in sources] == [object_id]
    assert sources[0].doi is None

    source_events = await _events_of_type(
        app_sessions, task_id, EvidenceNodeType.SOURCE.value
    )
    assert len(source_events) == 1
    finding_events = await _events_of_type(
        app_sessions, task_id, EvidenceNodeType.STUDY_FINDING.value
    )
    assert len(finding_events) == 1
    assert finding_events[0].payload["exact_quote"] == UPLOAD_QUOTE


async def test_a_refused_doi_closes_its_process_card_with_a_reason(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The live view's tool card must not sit on "等待结果…" forever.

    Round-5 regression: a DOI whose lookup was refused (retracted here) used
    to emit a SOURCE_REFUSED ledger event but no ``tool_result`` process
    event, so the front-end card that paired the ``tool_call`` stayed pending
    forever. Every refused query now closes its card with ``miss: true`` and
    the honest reason (CLAUDE.md 10).
    """
    task_id = await _seed(app_sessions)

    process_rows: list[tuple[str, object]] = []
    from packages.papers.acquisition import SourceAcquisition

    # Drive acquisition directly (not through run_task) so the process
    # callback is ours to capture; the ledger/DB side is the same machinery
    # the worker uses.
    async with app_sessions() as session:
        from packages.epistemo.budget import BudgetTracker, ResearchBudget

        acquirer = SourceAcquisition(
            session,
            _ProviderGateway(),
            task_id,
            BudgetTracker(
                limits=ResearchBudget(
                    wall_clock_minutes=60,
                    model_cost_usd=Decimal("10.0000"),
                    tool_call_limit=100,
                    source_limit=50,
                )
            ),
            on_process=lambda kind, payload: process_rows.append((kind, payload)),
        )
        result = await acquirer.acquire(
            [(Seat.CAUSAL_SCIENTIST, f"doi {RETRACTED_DOI}")]
        )
        await session.commit()

    assert result.acquired == ()
    tool_calls = [row for row in process_rows if row[0] == "tool_call"]
    tool_results = [row for row in process_rows if row[0] == "tool_result"]
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    payload = tool_results[0][1]
    assert isinstance(payload, dict)
    assert payload["miss"] is True
    assert payload["reason"] == "source is retracted"
