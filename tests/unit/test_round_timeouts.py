from __future__ import annotations

from packages.council.contracts import Seat
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.epistemo.orchestrator import RoundResult, run_round


async def test_timeout_marks_seat_absent_and_continues() -> None:
    result = await run_round(
        TaskPhase.PRECOMMITMENT,
        timed_out_seats=frozenset({Seat.REPLICATION_SCIENTIST}),
    )
    assert isinstance(result, RoundResult)
    assert len(result.completed_seats) == 6
    assert Seat.REPLICATION_SCIENTIST in result.absent_seats
    assert result.status == TaskStatus.DEGRADED_RUNNING


async def test_multiple_timeouts_still_degraded() -> None:
    result = await run_round(
        TaskPhase.ACQUISITION,
        timed_out_seats=frozenset(
            {Seat.REPLICATION_SCIENTIST, Seat.BOUNDARY_SCIENTIST}
        ),
    )
    assert result.status == TaskStatus.DEGRADED_RUNNING
    assert len(result.completed_seats) == 5
