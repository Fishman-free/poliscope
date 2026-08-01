from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from packages.council.contracts import ALL_SEATS, Seat
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    TaskPhase,
    TaskStatus,
)


@dataclass(frozen=True, slots=True)
class RoundResult:
    round_id: UUID
    phase: TaskPhase
    completed_seats: frozenset[Seat]
    absent_seats: frozenset[Seat]
    status: TaskStatus
    next_phase: TaskPhase | None = None
    unfilled_slots: tuple[str, ...] = ()


def _next_phase(current: TaskPhase) -> TaskPhase | None:
    try:
        idx = PHASE_SEQUENCE.index(current)
        if idx + 1 < len(PHASE_SEQUENCE):
            return PHASE_SEQUENCE[idx + 1]
        return None
    except ValueError:
        return None


async def run_round(
    phase: TaskPhase,
    failing_seat: Seat | None = None,
    timed_out_seats: frozenset[Seat] = frozenset(),
) -> RoundResult:
    """Execute a round for all seats, handling degradation."""
    excluded = set(timed_out_seats)
    if failing_seat is not None:
        excluded.add(failing_seat)
    completed = frozenset(seat for seat in ALL_SEATS if seat not in excluded)
    absent = frozenset(excluded)

    has_absence = bool(excluded)
    next_phase = _next_phase(phase)

    status = (
        TaskStatus.DEGRADED_RUNNING
        if has_absence
        else TaskStatus.QUEUED
    )

    unfilled: list[str] = []
    if has_absence and next_phase is not None:
        for seat in absent:
            unfilled.append(f"{seat.value}@{phase.value}")

    return RoundResult(
        round_id=uuid4(),
        phase=phase,
        completed_seats=completed,
        absent_seats=absent,
        status=status,
        next_phase=next_phase,
        unfilled_slots=tuple(unfilled),
    )
