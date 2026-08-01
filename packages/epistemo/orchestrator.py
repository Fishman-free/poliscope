"""Drives one research task through the seven council rounds.

CLAUDE.md 4 fixes the order of the protocol and CLAUDE.md 10 requires the phase
sequence to be an explicit state machine rather than an implicit one. This module
is where those two meet: it walks :data:`PHASE_SEQUENCE`, asks
:func:`runner_for` for the round, and appends whatever the round produced to the
Scientific Event Ledger.

**It does not write the Evidence Graph.** CLAUDE.md 5.3 gives that job to the
Graph Projector alone, and the projector runs under a different database identity
in a different transaction. The orchestrator's output is ledger events; turning
them into nodes is the worker's next step, after this one commits.

**A round that fails does not end the task.** CLAUDE.md 10 requires a single
seat's failure to degrade the run rather than abort it, so an exception inside a
round is recorded as a phase failure and an unfilled evidence slot, and the next
phase still runs. The task then finishes as COMPLETED_WITH_GAPS, which is the
honest description of what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from packages.council.contracts import ALL_SEATS, Seat
from packages.council.rounds.registry import (
    PHASE_COMPLETED,
    PHASE_STARTED,
    PhaseContext,
    PhaseOutcome,
    SeatDeliberator,
    UnavailableDeliberator,
    runner_for,
)
from packages.epistemo.budget import BudgetTracker
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    TaskPhase,
    TaskStatus,
)
from packages.epistemo.state_machine import TaskStateMachine
from packages.epistemo.stopping import StopReason, decide_stop

PHASE_FAILED = "PHASE_FAILED"
PHASE_SKIPPED = "PHASE_SKIPPED"

# Seats are asked in a fixed order so that a replay produces the same event
# sequence. A set iteration order would differ between processes and break the
# idempotency keys' promise of stability.
ORDERED_SEATS: tuple[Seat, ...] = tuple(sorted(ALL_SEATS, key=lambda seat: seat.value))


@dataclass(frozen=True, slots=True)
class RoundResult:
    """Outcome of one simulated round.

    Retained for the degradation and timeout tests, which exercise the rule that
    an absent seat degrades the run instead of stopping it, without needing a
    database.
    """

    round_id: UUID
    phase: TaskPhase
    completed_seats: frozenset[Seat]
    absent_seats: frozenset[Seat]
    status: TaskStatus
    next_phase: TaskPhase | None = None
    unfilled_slots: tuple[str, ...] = ()


def _next_phase(current: TaskPhase) -> TaskPhase | None:
    try:
        index = PHASE_SEQUENCE.index(current)
    except ValueError:
        return None
    if index + 1 < len(PHASE_SEQUENCE):
        return PHASE_SEQUENCE[index + 1]
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

    next_phase = _next_phase(phase)
    status = TaskStatus.DEGRADED_RUNNING if excluded else TaskStatus.QUEUED

    unfilled: tuple[str, ...] = ()
    if excluded and next_phase is not None:
        unfilled = tuple(
            f"{seat.value}@{phase.value}"
            for seat in sorted(absent, key=lambda item: item.value)
        )

    return RoundResult(
        round_id=uuid4(),
        phase=phase,
        completed_seats=completed,
        absent_seats=absent,
        status=status,
        next_phase=next_phase,
        unfilled_slots=unfilled,
    )


class EventSink(Protocol):
    """The ledger, narrowed to what the orchestrator is allowed to do with it.

    Append only. There is no update and no delete here because CLAUDE.md 5.3
    forbids both, and a narrow protocol makes that visible at the call site
    rather than only in the database grants.
    """

    async def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
        *,
        evidence_level: str | None = None,
        source_id: UUID | None = None,
        finding_id: UUID | None = None,
        claim_id: UUID | None = None,
    ) -> object: ...


@dataclass
class TaskRunReport:
    """What one pass over the protocol did.

    ``unfilled_slots`` is the part that matters. CLAUDE.md 10 forbids reporting a
    complete result when evidence is missing, so this list is what the report
    layer must surface rather than quietly drop.
    """

    task_id: UUID
    final_status: TaskStatus = TaskStatus.QUEUED
    phases_run: tuple[TaskPhase, ...] = ()
    phases_skipped: tuple[TaskPhase, ...] = ()
    events_appended: int = 0
    unfilled_slots: tuple[str, ...] = ()
    absent_seats: frozenset[Seat] = frozenset()
    failures: tuple[str, ...] = ()
    stop_reason: StopReason = StopReason.CONTINUE

    @property
    def has_gaps(self) -> bool:
        return bool(self.unfilled_slots or self.failures or self.absent_seats)


@dataclass
class _Accumulator:
    """Mutable working state for one run, kept out of the report."""

    unfilled: list[str] = field(default_factory=list)
    absent: set[Seat] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)
    carried: dict[str, object] = field(default_factory=dict)
    events: int = 0


class CouncilOrchestrator:
    """Runs the seven rounds for one task against one ledger.

    The instance holds no task state between calls, so a resumed task is simply
    a second :meth:`run` with the same ``task_id``: every event carries a
    replay-stable idempotency key, and the ledger turns the repeat into a no-op.
    """

    def __init__(
        self,
        ledger: EventSink,
        budget: BudgetTracker,
        deliberator: SeatDeliberator | None = None,
        seats: tuple[Seat, ...] = ORDERED_SEATS,
    ) -> None:
        self._ledger = ledger
        self._budget = budget
        self._deliberator = (
            deliberator if deliberator is not None else UnavailableDeliberator()
        )
        self._seats = seats

    async def run(
        self,
        task_id: UUID,
        question: str,
        confirmed_claims: tuple[UUID, ...] = (),
    ) -> TaskRunReport:
        machine = TaskStateMachine()
        state = _Accumulator()
        report = TaskRunReport(task_id=task_id)
        run_phases: list[TaskPhase] = []
        skipped: list[TaskPhase] = []
        stop = StopReason.CONTINUE

        for phase in PHASE_SEQUENCE:
            machine.transition_to(phase)
            if stop is StopReason.CONTINUE:
                stop = self._check_budget()
            if stop is not StopReason.CONTINUE:
                # A phase that never ran is a gap, not a silent omission.
                skipped.append(phase)
                state.unfilled.append(f"{phase.value}:not_reached")
                await self._append_skip(task_id, phase, stop)
                continue
            await self._run_phase(task_id, phase, question, confirmed_claims, state)
            run_phases.append(phase)

        if state.absent:
            machine.transition_to(TaskStatus.DEGRADED_RUNNING)

        report.phases_run = tuple(run_phases)
        report.phases_skipped = tuple(skipped)
        report.events_appended = state.events
        report.unfilled_slots = tuple(state.unfilled)
        report.absent_seats = frozenset(state.absent)
        report.failures = tuple(state.failures)
        report.stop_reason = stop
        report.final_status = (
            TaskStatus.COMPLETED_WITH_GAPS if report.has_gaps else TaskStatus.COMPLETED
        )
        machine.transition_to(report.final_status)
        for slot in report.unfilled_slots:
            self._budget.mark_unfilled_slot(slot)
        return report

    def _check_budget(self) -> StopReason:
        """Ask whether there is enough budget left to run another phase.

        Evidence saturation deliberately cannot stop the run here: CLAUDE.md 4
        requires all seven rounds, and a round that produced nothing is a finding
        about the evidence, not a reason to skip the falsifier.
        """
        decision = decide_stop(
            no_new_information_rounds=0,
            budget_remaining=self._budget.tool_calls_remaining,
        )
        return decision.reason

    async def _append_skip(
        self,
        task_id: UUID,
        phase: TaskPhase,
        stop: StopReason,
    ) -> None:
        await self._ledger.append(
            task_id=task_id,
            event_type=PHASE_SKIPPED,
            payload={"phase": phase.value, "reason": stop.value},
            idempotency_key=f"{phase.value}:skipped",
        )

    async def _run_phase(
        self,
        task_id: UUID,
        phase: TaskPhase,
        question: str,
        confirmed_claims: tuple[UUID, ...],
        state: _Accumulator,
    ) -> None:
        await self._ledger.append(
            task_id=task_id,
            event_type=PHASE_STARTED,
            payload={"phase": phase.value},
            idempotency_key=f"{phase.value}:started",
        )
        state.events += 1

        context = PhaseContext(
            task_id=task_id,
            phase=phase,
            seats=self._seats,
            question=question,
            confirmed_claims=confirmed_claims,
            deliberator=self._deliberator,
            carried=dict(state.carried),
        )
        try:
            outcome = await runner_for(phase)(context)
        except Exception as error:  # noqa: BLE001 - recorded, never swallowed
            # CLAUDE.md 10: one round's failure degrades the run. The reason is
            # written to the ledger so the researcher sees why the round is
            # missing rather than finding an unexplained hole in the report.
            state.failures.append(f"{phase.value}: {error!r}")
            state.unfilled.append(f"{phase.value}:round_failed")
            await self._ledger.append(
                task_id=task_id,
                event_type=PHASE_FAILED,
                payload={"phase": phase.value, "error": repr(error)},
                idempotency_key=f"{phase.value}:failed",
            )
            state.events += 1
            return

        await self._emit(task_id, outcome, state)
        state.unfilled.extend(outcome.unfilled_slots)
        state.absent.update(outcome.absent_seats)
        state.carried.update(outcome.carry)

        await self._ledger.append(
            task_id=task_id,
            event_type=PHASE_COMPLETED,
            payload={
                "phase": phase.value,
                "unfilled_slots": list(outcome.unfilled_slots),
                "absent_seats": sorted(seat.value for seat in outcome.absent_seats),
            },
            idempotency_key=f"{phase.value}:completed",
        )
        state.events += 1

    async def _emit(
        self,
        task_id: UUID,
        outcome: PhaseOutcome,
        state: _Accumulator,
    ) -> None:
        for event in outcome.events:
            await self._ledger.append(
                task_id=task_id,
                event_type=event.event_type,
                payload=event.payload,
                idempotency_key=event.idempotency_key,
                evidence_level=event.evidence_level,
                source_id=event.source_id,
                finding_id=event.finding_id,
                claim_id=event.claim_id,
            )
            state.events += 1


__all__ = [
    "PHASE_FAILED",
    "PHASE_SKIPPED",
    "CouncilOrchestrator",
    "EventSink",
    "RoundResult",
    "TaskRunReport",
    "run_round",
]
