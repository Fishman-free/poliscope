"""The worker path, end to end, against a real database and real roles.

These tests exist because every layer below was individually correct while
nothing connected them: the rounds had no caller, the orchestrator drove
nothing, and the worker returned a hardcoded dictionary. What is asserted here is
the join -- that a QUEUED task produces ledger events, that those events reach
the graph only through the projector, and that the gaps are reported rather than
hidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import TaskNotRunnable, run_task
from apps.worker.main import WorkerContext, drain
from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    PHASE_STARTED,
    SEAT_UNAVAILABLE,
    PhaseContext,
)
from packages.epistemo.contracts import PHASE_SEQUENCE, TaskPhase, TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import (
    GraphNodeModel,
    ScientificEventModel,
)
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED

QUESTION = "Does adolescent social media use cause depressive symptoms?"


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
    *,
    tool_call_limit: int = 100,
) -> tuple[UUID, UUID]:
    """Create one QUEUED task with one confirmed claim. Returns (task, claim)."""
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="worker_pipeline_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=tool_call_limit,
                source_limit=50,
                user_evidence={},
            )
        )
        await session.flush()
        session.add(
            AtomicClaimModel(
                id=claim_id,
                task_id=task_id,
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="worker_pipeline_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def _events(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[ScientificEventModel]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel)
            .where(ScientificEventModel.task_id == task_id)
            .order_by(ScientificEventModel.sequence)
        )
        return list(result.scalars())


async def _nodes(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[GraphNodeModel]:
    async with sessions() as session:
        result = await session.execute(
            select(GraphNodeModel).where(GraphNodeModel.task_id == task_id)
        )
        return list(result.scalars())


async def _status(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> str:
    async with sessions() as session:
        value = await session.scalar(
            select(ResearchTaskModel.status).where(
                ResearchTaskModel.task_id == task_id
            )
        )
        return str(value)


class _ScriptedDeliberator:
    """Stands in for the model layer with fixed, replay-stable outputs.

    Deterministic on purpose: the point of the tests below is the pipeline, and a
    deliberator whose answers varied would make an idempotency failure look like
    a flaky test instead of the bug it is.
    """

    def __init__(self, blindspot_id: UUID) -> None:
        self._blindspot_id = blindspot_id

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat.value} sees weak correlational support",
                "confidence": 0.4,
                "update_condition": "a preregistered cohort study",
            }
        if phase is TaskPhase.ACQUISITION:
            return {"requests": [f"cohort studies for {seat.value}"]}
        if phase is TaskPhase.BLINDSPOT_BOUNTY and seat is Seat.ADVERSARY_FALSIFIER:
            return {
                "blindspots": [
                    {
                        "id": str(self._blindspot_id),
                        "statement": "Screen time is self-reported and misremembered.",
                        "impact": "0.9",
                        "uncertainty": "0.8",
                        "investigability": "0.7",
                        "novelty": "0.6",
                        "normalized_cost": "0.2",
                    }
                ]
            }
        return None


async def test_a_queued_task_runs_every_phase_of_the_protocol(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 4 fixes seven rounds plus reporting. None may be skipped."""
    task_id, _ = await _seed_queued_task(app_sessions)

    result = await run_task(app_sessions, projector_sessions, task_id)

    assert result.run.phases_run == PHASE_SEQUENCE
    assert result.run.phases_skipped == ()
    started = {
        str(event.payload["phase"])
        for event in await _events(app_sessions, task_id)
        if event.event_type == PHASE_STARTED
    }
    assert started == {phase.value for phase in PHASE_SEQUENCE}


async def test_an_unavailable_seat_is_reported_not_invented(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """With no model provider, the honest result is gaps -- not a full report.

    CLAUDE.md 7 requires the system to admit what it does not know. A run with no
    deliberator that reported COMPLETED would be claiming seven scientists
    deliberated when none did.
    """
    task_id, _ = await _seed_queued_task(app_sessions)

    result = await run_task(app_sessions, projector_sessions, task_id)

    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert result.run.absent_seats == frozenset(Seat)
    assert await _status(app_sessions, task_id) == TaskStatus.COMPLETED_WITH_GAPS
    unavailable = [
        event
        for event in await _events(app_sessions, task_id)
        if event.event_type == SEAT_UNAVAILABLE
    ]
    assert {str(event.payload["seat"]) for event in unavailable} == {
        seat.value for seat in Seat
    }


async def test_process_events_never_become_graph_nodes(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 5.1: a process node is not automatically scientific evidence."""
    task_id, _ = await _seed_queued_task(app_sessions)

    await run_task(app_sessions, projector_sessions, task_id)

    node_types = {node.node_type for node in await _nodes(app_sessions, task_id)}
    assert node_types <= {item.value for item in EvidenceNodeType}
    assert PHASE_STARTED not in node_types
    assert SEAT_UNAVAILABLE not in node_types


async def test_the_research_question_reaches_the_graph(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The one node a run always produces, so the map is never empty."""
    task_id, _ = await _seed_queued_task(app_sessions)

    await run_task(app_sessions, projector_sessions, task_id)

    questions = [
        node
        for node in await _nodes(app_sessions, task_id)
        if node.node_type == EvidenceNodeType.RESEARCH_QUESTION.value
    ]
    assert len(questions) == 1
    assert questions[0].payload["question"] == QUESTION


async def test_a_nominated_blindspot_is_scored_and_admitted(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Blindspot discovery is the product's core value, so it must reach the map."""
    task_id, _ = await _seed_queued_task(app_sessions)
    blindspot_id = uuid4()

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(blindspot_id),
    )

    blindspots = [
        node
        for node in await _nodes(app_sessions, task_id)
        if node.node_type == EvidenceNodeType.BLINDSPOT.value
    ]
    assert [node.id for node in blindspots] == [blindspot_id]
    # 0.30*0.9 + 0.25*0.8 + 0.20*0.7 + 0.15*0.6 + 0.10*(1-0.2)
    assert blindspots[0].payload["score"] == "0.7800"


async def test_a_finished_task_is_not_run_again(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A second pass must not reopen a terminal status and erase its gaps."""
    task_id, _ = await _seed_queued_task(app_sessions)
    await run_task(app_sessions, projector_sessions, task_id)

    with pytest.raises(TaskNotRunnable):
        await run_task(app_sessions, projector_sessions, task_id)


async def test_replaying_a_requeued_task_duplicates_nothing(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10 wants resume, which means a re-run must be a no-op.

    Every idempotency key is derived from the phase and the seat, so the second
    pass matches the first row for row. If a round ever mints a random id into a
    payload, this fails with an EventConflict rather than silently double
    counting the evidence.
    """
    task_id, _ = await _seed_queued_task(app_sessions)
    blindspot_id = uuid4()
    deliberator = _ScriptedDeliberator(blindspot_id)
    await run_task(app_sessions, projector_sessions, task_id, deliberator)
    first = [event.id for event in await _events(app_sessions, task_id)]
    nodes_before = len(await _nodes(app_sessions, task_id))

    async with app_sessions() as session:
        await session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.QUEUED)
        )
        await session.commit()

    await run_task(app_sessions, projector_sessions, task_id, deliberator)

    assert [event.id for event in await _events(app_sessions, task_id)] == first
    assert len(await _nodes(app_sessions, task_id)) == nodes_before


async def test_an_exhausted_budget_skips_phases_and_says_so(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10 forbids faking a complete result when the budget runs out."""
    task_id, _ = await _seed_queued_task(app_sessions, tool_call_limit=0)

    result = await run_task(app_sessions, projector_sessions, task_id)

    assert result.run.phases_run == ()
    assert result.run.phases_skipped == PHASE_SEQUENCE
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert all(slot.endswith(":not_reached") for slot in result.run.unfilled_slots)


async def test_the_worker_picks_up_a_queued_task_on_its_own(
    migrated_db: str,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The queue is the part a user actually depends on: they queue and wait."""
    from tests.conftest import (
        APP_PASSWORD,
        APP_ROLE,
        PROJECTOR_PASSWORD,
        PROJECTOR_ROLE,
        _role_url,
    )

    task_id, _ = await _seed_queued_task(app_sessions)
    context = WorkerContext.from_urls(
        _role_url(migrated_db, APP_ROLE, APP_PASSWORD),
        _role_url(migrated_db, PROJECTOR_ROLE, PROJECTOR_PASSWORD),
    )
    try:
        results = await drain(context, limit=10)
    finally:
        await context.dispose()

    assert task_id in {result.task_id for result in results}
    assert await _status(app_sessions, task_id) == TaskStatus.COMPLETED_WITH_GAPS
