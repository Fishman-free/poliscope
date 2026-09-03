"""B7: a worker killed mid-council resumes from the last durable phase.

Before B7 the eight-phase deliberation was one uncommitted transaction: a
process killed during, say, EVIDENCE_EXCHANGE discarded PRECOMMITMENT and
ACQUISITION too, and the reclaim re-ran (and re-billed) every completed phase.
B7 commits a running checkpoint after every phase, so the reclaimed task skips
phases that already completed -- their deliberator is never called again --
while the interrupted phase is redone and the human gate before
JOINT_MODELING is never bypassed.

The crash is simulated with ``asyncio.CancelledError`` (a ``BaseException``,
exactly what a killed worker's task receives): it deliberately escapes the
orchestrator's per-phase ``except Exception`` degradation, just like a real
process death, while the already-committed phase checkpoints survive the
rollback of the interrupted phase's open transaction.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import ALL_SEATS, Seat
from packages.council.models import CouncilRoundModel
from packages.council.rounds.registry import PHASE_COMPLETED, PhaseContext
from packages.epistemo.contracts import PHASE_SEQUENCE, TaskPhase, TaskStatus
from packages.evidence.models import ScientificEventModel
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.research.service import ResearchService

QUESTION = "Does adolescent social media use cause depressive symptoms?"


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
) -> UUID:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="council_crash_resume_test",
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
                created_by="council_crash_resume_test",
            )
        )
        await session.commit()
    return task_id


class _CountingCrashDeliberator:
    """Deterministic answers, per-phase call counts, one crash on one phase.

    ``calls`` is cumulative across worker passes: it is the proof that a
    resumed run never re-invokes the seats of a phase that was durably
    committed before the crash.
    """

    def __init__(self, crash_on_phase: TaskPhase | None) -> None:
        self._crash_on_phase = crash_on_phase
        self._crashed = False
        self.calls: Counter[TaskPhase] = Counter()

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        self.calls[phase] += 1
        if (
            not self._crashed
            and self._crash_on_phase is not None
            and phase is self._crash_on_phase
        ):
            # A worker killed mid-call: BaseException, so the orchestrator's
            # ``except Exception`` degradation cannot turn it into a gap.
            self._crashed = True
            raise asyncio.CancelledError("simulated worker kill")
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
                "falsification_conditions": [
                    "A null effect in a preregistered RCT."
                ],
                "boundary_conditions": ["Western adolescent samples only."],
                "unresolved_conflicts": [],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            return {
                "final_judgment": f"{seat.value} confirms the correlational finding",
                "confidence": 0.5,
            }
        return None


async def _phase_completed_keys(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> dict[str, int]:
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id,
                    ScientificEventModel.event_type == PHASE_COMPLETED,
                )
            )
        )
    counts: Counter[str] = Counter(
        str(row.payload["phase"]) for row in rows
    )
    return dict(counts)


async def _council_round_phases(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> set[str]:
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(CouncilRoundModel.phase).where(
                    CouncilRoundModel.task_id == task_id
                )
            )
        )
    return {str(row) for row in rows}


async def _reset_to_queued(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> None:
    """Mirror ``recover_stale_running``: a killed run is requeued for reclaim."""
    async with sessions() as session:
        await session.execute(
            ResearchTaskModel.__table__.update()
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=TaskStatus.QUEUED)
        )
        await session.commit()


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


async def test_crash_after_gate_resumes_straight_to_completion(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A crash AFTER the human gate resumes straight to terminal.

    Pass 1 halts at the fixed gate (no crash); the researcher submits an
    explicit empty steer; pass 2 is then killed during FINAL_REJUDGMENT. Its
    gate-marked checkpoint makes the reclaimed pass 3 run the remaining phases
    straight through -- PRECOMMITMENT..BLINDSPOT_BOUNTY were committed before
    the kill and their seats are never deliberated again.
    """
    task_id = await _seed_queued_task(app_sessions)
    deliberator = _CountingCrashDeliberator(TaskPhase.FINAL_REJUDGMENT)

    # Pass 1: first run parks at the human gate.
    gate = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
    )
    assert gate.run.final_status is TaskStatus.AWAITING_COUNCIL_INPUT
    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()

    # Pass 2: killed during FINAL_REJUDGMENT after the gate was passed.
    with pytest.raises(asyncio.CancelledError):
        await run_task(
            app_sessions,
            projector_sessions,
            task_id,
            deliberator=deliberator,
        )

    completed_after_crash = await _phase_completed_keys(app_sessions, task_id)
    # Phases through JOINT_MODELING committed; FINAL_REJUDGMENT/REPORTING did
    # not survive (the kill rolled back their open transaction).
    assert TaskPhase.JOINT_MODELING.value in completed_after_crash
    assert TaskPhase.FINAL_REJUDGMENT.value not in completed_after_crash
    await _reset_to_queued(app_sessions, task_id)

    # Pass 3: the reclaimed run finishes without stopping at the gate again.
    result = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
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

    # Every phase committed before the kill was deliberated exactly once per
    # seat (seven seats), never re-run in the resumed pass.
    for phase in (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
        TaskPhase.JOINT_MODELING,
    ):
        assert deliberator.calls[phase] == len(ALL_SEATS), phase
    # The interrupted phase ran once in the killed pass and once in resume.
    assert deliberator.calls[TaskPhase.FINAL_REJUDGMENT] == len(ALL_SEATS) + 1

    # Every phase completed exactly once on the ledger (no duplicate events).
    final_completed = await _phase_completed_keys(app_sessions, task_id)
    assert final_completed == {phase.value: 1 for phase in PHASE_SEQUENCE}
    # The per-phase audit rows cover the whole protocol despite the crash.
    assert await _council_round_phases(app_sessions, task_id) == {
        phase.value for phase in PHASE_SEQUENCE
    }


async def test_crash_before_the_human_gate_halts_there_on_resume(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A crash before JOINT_MODELING must not let the resume skip the human gate."""
    task_id = await _seed_queued_task(app_sessions)
    deliberator = _CountingCrashDeliberator(TaskPhase.CROSS_EXAMINATION)

    with pytest.raises(asyncio.CancelledError):
        await run_task(
            app_sessions,
            projector_sessions,
            task_id,
            deliberator=deliberator,
        )
    await _reset_to_queued(app_sessions, task_id)

    resumed = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
    )
    # The resumed run reaches the fixed gate and parks there -- it does not run
    # straight through JOINT_MODELING just because a checkpoint exists.
    assert resumed.run.final_status is TaskStatus.AWAITING_COUNCIL_INPUT
    assert await _status(app_sessions, task_id) == TaskStatus.AWAITING_COUNCIL_INPUT
    assert deliberator.calls[TaskPhase.JOINT_MODELING] == 0

    # Steering (including the explicit empty "no intervention") then finishes.
    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()

    final = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
    )
    assert final.run.final_status in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    )
    assert final.run.phases_run == PHASE_SEQUENCE
    final_completed = await _phase_completed_keys(app_sessions, task_id)
    assert final_completed == {
        phase.value: 1 for phase in PHASE_SEQUENCE
    }
