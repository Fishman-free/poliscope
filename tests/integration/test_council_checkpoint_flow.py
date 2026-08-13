"""End-to-end coverage of the BLINDSPOT_BOUNTY -> JOINT_MODELING checkpoint.

Plan phase 8.2/8.3. This is the join no unit test can cover: a task must
really halt at ``AWAITING_COUNCIL_INPUT`` with a persisted checkpoint row,
the council-preview read must reflect exactly what the halted run produced,
``ResearchService.submit_council_guidance`` (both a real steer and the
deliberate "no intervention" empty string) must requeue it, and a second
worker pass must resume from JOINT_MODELING rather than re-running the first
five phases -- all against a real database and real roles, per CLAUDE.md
12.3's end-to-end requirement.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.routers.workspace import _seats
from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.council.rounds.registry import PHASE_STARTED, PhaseContext
from packages.epistemo.contracts import PHASE_SEQUENCE, TaskPhase, TaskStatus
from packages.evidence.models import ScientificEventModel
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.research.service import (
    InvalidCouncilGuidanceState,
    ResearchService,
)

QUESTION = "Does adolescent social media use cause depressive symptoms?"


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="council_checkpoint_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
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
                created_by="council_checkpoint_test",
            )
        )
        await session.commit()
    return task_id, claim_id


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


async def _checkpoint(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> dict[str, object] | None:
    async with sessions() as session:
        return await session.scalar(
            select(ResearchTaskModel.council_checkpoint).where(
                ResearchTaskModel.task_id == task_id
            )
        )


class _ScriptedDeliberator:
    """Answers every phase deterministically, including past the checkpoint.

    Deterministic outputs, same rationale as
    ``tests/integration/test_worker_pipeline.py``'s fixture: a flaky answer
    here would make a resume bug look like a model-variance fluke.
    """

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
        if phase is TaskPhase.JOINT_MODELING:
            return {
                "strongest_opposition_refs": [],
                "falsification_conditions": ["A null effect in a preregistered RCT."],
                "boundary_conditions": ["Western adolescent samples only."],
                "unresolved_conflicts": [],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            return {
                "final_judgment": f"{seat.value} confirms the correlational finding",
                "confidence": 0.5,
            }
        return None


async def test_a_queued_task_halts_before_joint_modeling_with_a_checkpoint(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, _ = await _seed_queued_task(app_sessions)

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )

    assert result.run.final_status == TaskStatus.AWAITING_COUNCIL_INPUT
    assert result.run.phases_run == (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
    )
    assert await _status(app_sessions, task_id) == TaskStatus.AWAITING_COUNCIL_INPUT
    stored = await _checkpoint(app_sessions, task_id)
    assert stored is not None
    assert stored["guidance"] is None

    async with app_sessions() as session:
        events = list(
            await session.scalars(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id
                )
            )
        )
    started = {
        str(event.payload["phase"])
        for event in events
        if event.event_type == PHASE_STARTED
    }
    assert started == {
        TaskPhase.PRECOMMITMENT.value,
        TaskPhase.ACQUISITION.value,
        TaskPhase.EVIDENCE_EXCHANGE.value,
        TaskPhase.CROSS_EXAMINATION.value,
        TaskPhase.BLINDSPOT_BOUNTY.value,
    }
    # JOINT_MODELING never started -- the halt happened strictly before it.
    assert TaskPhase.JOINT_MODELING.value not in started


async def test_council_preview_reflects_the_halted_run(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, _ = await _seed_queued_task(app_sessions)
    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )

    async with app_sessions() as session:
        seats = await _seats(session, task_id)

    by_seat = {row["seat"]: row for row in seats}
    for seat in Seat:
        assert seat.value in by_seat
        precommitment = by_seat[seat.value]["precommitment"]
        assert isinstance(precommitment, Mapping)
        assert precommitment["confidence"] == 0.4
        assert precommitment["update_condition"] == "a preregistered cohort study"
        # JOINT_MODELING/FINAL_REJUDGMENT have not run yet -- the preview must
        # not show a final judgment that does not exist.
        assert by_seat[seat.value]["final_judgment"] is None


async def test_guidance_can_only_be_submitted_while_halted(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, _ = await _seed_queued_task(app_sessions)
    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        with pytest.raises(InvalidCouncilGuidanceState):
            await service.submit_council_guidance(task_id, "too early")
        await session.commit()


@pytest.mark.parametrize("guidance_text", ["重点关注跨文化边界条件", ""])
async def test_guidance_including_empty_resumes_to_completion(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    guidance_text: str,
) -> None:
    """CLAUDE.md 4/8: declining to steer (``""``) is as valid as steering."""
    task_id, _ = await _seed_queued_task(app_sessions)
    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )
    assert await _status(app_sessions, task_id) == TaskStatus.AWAITING_COUNCIL_INPUT

    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        new_status = await service.submit_council_guidance(task_id, guidance_text)
        await session.commit()
    assert new_status == TaskStatus.QUEUED

    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )

    assert result.run.final_status in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    )
    assert result.run.phases_run == PHASE_SEQUENCE
    assert await _status(app_sessions, task_id) in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    )
    # Terminal tasks keep the rewind snapshot so 「从断点处研究」 can see
    # which protocol phases actually ran. A later reader must not treat it
    # as AWAITING_COUNCIL_INPUT — that is the status column, not the JSON.
    stored = await _checkpoint(app_sessions, task_id)
    assert stored is not None
    assert stored["run_phases"] == [phase.value for phase in PHASE_SEQUENCE]


async def test_resuming_does_not_replay_the_five_phases_before_the_checkpoint(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, _ = await _seed_queued_task(app_sessions)
    first = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )
    async with app_sessions() as session:
        events_at_halt = list(
            await session.scalars(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id
                )
            )
        )

    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()

    second = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=_ScriptedDeliberator(),
    )

    # The report aggregates the whole run: run() seeds run_phases from
    # resume_from's own five before the loop starts (orchestrator.py's
    # `list(resume_from.run_phases) if resume_from else []`), and only the
    # three phases this pass actually executes are appended to that -- so a
    # resumed report's phases_run is the full eight-phase sequence, not just
    # what this pass touched.
    assert second.run.phases_run == PHASE_SEQUENCE
    async with app_sessions() as session:
        events_after_resume = list(
            await session.scalars(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id
                )
            )
        )
    # Every event from the halted run is still there untouched, plus new ones
    # from the resumed phases -- nothing from before the checkpoint was
    # re-appended (idempotency keys would have deduplicated it silently even
    # if it had been re-run, so the phase list above is the real proof; this
    # is the visible corroboration).
    assert {event.id for event in events_at_halt} <= {
        event.id for event in events_after_resume
    }
    assert len(events_after_resume) > len(events_at_halt)
    assert first.run.final_status == TaskStatus.AWAITING_COUNCIL_INPUT
