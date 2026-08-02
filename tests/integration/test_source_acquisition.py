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
from uuid import UUID, uuid4

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
from packages.papers.models import SourceModel
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
