from __future__ import annotations

from packages.council.contracts import Seat
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.epistemo.orchestrator import RoundResult, run_round


async def test_one_seat_failure_continues_current_round() -> None:
    result = await run_round(
        TaskPhase.EVIDENCE_EXCHANGE,
        failing_seat=Seat.MEASUREMENT_SCIENTIST,
    )
    assert isinstance(result, RoundResult)
    assert len(result.completed_seats) == 6
    assert result.status == TaskStatus.DEGRADED_RUNNING
    assert result.next_phase == TaskPhase.CROSS_EXAMINATION


async def test_all_seats_complete_normal_transition() -> None:
    result = await run_round(TaskPhase.PRECOMMITMENT)
    assert isinstance(result, RoundResult)
    assert len(result.completed_seats) == 7
    assert result.status == TaskStatus.QUEUED
    assert result.next_phase == TaskPhase.ACQUISITION


def test_suite() -> None:
    import asyncio
    asyncio.run(test_one_seat_failure_continues_current_round())
    asyncio.run(test_all_seats_complete_normal_transition())
