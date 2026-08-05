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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from packages.council.contracts import ALL_SEATS, Seat
from packages.council.rounds.registry import (
    PHASE_COMPLETED,
    PHASE_STARTED,
    FindingExtractor,
    KnowledgeDocumentLike,
    KnowledgeSearcher,
    PhaseContext,
    PhaseOutcome,
    SeatDeliberator,
    SourceAcquirer,
    UnavailableDeliberator,
    runner_for,
)
from packages.epistemo.budget import BudgetTracker
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    CouncilCheckpoint,
    TaskPhase,
    TaskStatus,
)
from packages.epistemo.state_machine import TaskStateMachine
from packages.epistemo.stopping import StopReason, decide_stop
from packages.evidence.lifecycle import QuarantinedNode
from packages.memory.council_memory import CouncilMemory

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
    # Populated only when final_status is AWAITING_COUNCIL_INPUT -- the caller
    # (apps/worker/jobs.py) persists this to the checkpoint column so a later
    # call can pass it back in as run()'s resume_from argument.
    checkpoint: CouncilCheckpoint | None = None

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
        memory: CouncilMemory | None = None,
        acquirer: SourceAcquirer | None = None,
        finding_extractor: FindingExtractor | None = None,
    ) -> None:
        self._ledger = ledger
        self._budget = budget
        self._deliberator = (
            deliberator if deliberator is not None else UnavailableDeliberator()
        )
        self._seats = seats
        self._memory = memory
        self._acquirer = acquirer
        self._finding_extractor = finding_extractor

    async def run(
        self,
        task_id: UUID,
        question: str,
        confirmed_claims: tuple[UUID, ...] = (),
        quarantined: tuple[QuarantinedNode, ...] = (),
        pdf_object_ids: tuple[UUID, ...] = (),
        user_dois: tuple[str, ...] = (),
        knowledge_documents: tuple[KnowledgeDocumentLike, ...] = (),
        knowledge_search: KnowledgeSearcher | None = None,
        researcher_skills: tuple[tuple[str, str], ...] = (),
        stop_before: TaskPhase | None = None,
        resume_from: CouncilCheckpoint | None = None,
        council_guidance: str | None = None,
    ) -> TaskRunReport:
        """Run the protocol, optionally halting at (or resuming from) a checkpoint.

        ``stop_before`` and ``resume_from`` both default to ``None``, so every
        existing caller that does not pass them gets exactly the prior
        behaviour: all eight phases run in one pass. Plan phase 8's fixed
        BLINDSPOT_BOUNTY -> JOINT_MODELING checkpoint is the only user of
        either argument today -- the worker calls ``run(stop_before=
        TaskPhase.JOINT_MODELING)`` first, persists the returned
        ``report.checkpoint``, and later calls ``run(resume_from=<that
        checkpoint>)`` to finish the remaining phases without re-running the
        ones already on the ledger.

        ``council_guidance`` is the human's advisory text collected at that
        checkpoint (plan phase 8.3). It only ever reaches
        :class:`~packages.council.rounds.registry.PhaseContext` -- passed
        through unconditionally for every phase, but rendered into a prompt
        only when that phase is JOINT_MODELING (see
        ``packages/council/deliberation.py::_user_prompt``). It is never
        written into ``state.carried``, so it cannot leak into any other
        phase's prompt and cannot affect Evidence Gate, Claim adoption, or
        DissentCertificate/DebateCapsule construction.
        """
        machine = TaskStateMachine()
        state = _Accumulator()
        report = TaskRunReport(task_id=task_id)
        run_phases: list[TaskPhase] = (
            list(resume_from.run_phases) if resume_from else []
        )
        already_run = frozenset(run_phases)
        skipped: list[TaskPhase] = []
        stop = StopReason.CONTINUE

        if resume_from is not None:
            state.carried.update(dict(resume_from.carried))
            state.unfilled.extend(resume_from.unfilled)
            state.absent.update(resume_from.absent_seats)
            state.failures.extend(resume_from.failures)
            state.events = resume_from.events_appended
            # Fast-forward the state machine through the already-completed
            # phases, in order, without re-emitting anything -- this is what
            # satisfies _transition_to_phase's strict "current + 1" rule once
            # the loop below reaches the first phase after the checkpoint.
            for phase in resume_from.run_phases:
                machine.transition_to(phase)

        if self._memory is not None:
            await self._memory.open(self._seats, question)

        for phase in PHASE_SEQUENCE:
            if phase in already_run:
                continue
            if (
                stop_before is not None
                and phase == stop_before
                and stop is StopReason.CONTINUE
            ):
                # Halt here rather than run the phase. Budget exhaustion (the
                # `stop is not CONTINUE` case) takes priority and falls
                # through to the ordinary skip path below instead -- a run
                # that already died of budget exhaustion has nothing left for
                # a human to steer, and should finish as COMPLETED_WITH_GAPS
                # rather than hang waiting for input that cannot help it.
                report.phases_run = tuple(run_phases)
                report.phases_skipped = tuple(skipped)
                report.events_appended = state.events
                report.unfilled_slots = tuple(state.unfilled)
                report.absent_seats = frozenset(state.absent)
                report.failures = tuple(state.failures)
                report.stop_reason = stop
                report.final_status = TaskStatus.AWAITING_COUNCIL_INPUT
                report.checkpoint = CouncilCheckpoint(
                    run_phases=tuple(run_phases),
                    carried=state.carried,
                    unfilled=tuple(state.unfilled),
                    absent_seats=tuple(
                        sorted(state.absent, key=lambda seat: seat.value)
                    ),
                    failures=tuple(state.failures),
                    events_appended=state.events,
                )
                machine.transition_to(TaskStatus.AWAITING_COUNCIL_INPUT)
                return report
            machine.transition_to(phase)
            if stop is StopReason.CONTINUE:
                stop = self._check_budget()
            if stop is not StopReason.CONTINUE:
                # A phase that never ran is a gap, not a silent omission.
                skipped.append(phase)
                state.unfilled.append(f"{phase.value}:not_reached")
                await self._append_skip(task_id, phase, stop)
                continue
            await self._run_phase(
                task_id,
                phase,
                question,
                confirmed_claims,
                quarantined,
                pdf_object_ids,
                user_dois,
                knowledge_documents,
                knowledge_search,
                researcher_skills,
                state,
                council_guidance,
            )
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
        quarantined: tuple[QuarantinedNode, ...],
        pdf_object_ids: tuple[UUID, ...],
        user_dois: tuple[str, ...],
        knowledge_documents: tuple[KnowledgeDocumentLike, ...],
        knowledge_search: KnowledgeSearcher | None,
        researcher_skills: tuple[tuple[str, str], ...],
        state: _Accumulator,
        council_guidance: str | None = None,
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
            recall=await self._recall(),
            acquirer=self._acquirer,
            finding_extractor=self._finding_extractor,
            quarantined=quarantined,
            pdf_object_ids=pdf_object_ids,
            user_dois=user_dois,
            knowledge_documents=knowledge_documents,
            knowledge_search=knowledge_search,
            researcher_skills=researcher_skills,
            guidance=council_guidance,
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
        await self._remember(phase, outcome)

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

    async def _recall(self) -> Mapping[Seat, str]:
        if self._memory is None:
            return {}
        return await self._memory.recall(self._seats)

    async def _remember(self, phase: TaskPhase, outcome: PhaseOutcome) -> None:
        """Record what the round did in each participating seat's own memory.

        An absent seat gets nothing: writing "you were unavailable" into its
        private memory would let a later round mistake the record of a gap for
        the seat's own reasoning. The gap is already on the ledger, where the
        researcher can see it.
        """
        if self._memory is None:
            return
        summary = (
            f"{phase.value}: {len(outcome.events)} events, "
            f"{len(outcome.unfilled_slots)} unfilled slots"
        )
        for seat in self._seats:
            if seat in outcome.absent_seats:
                continue
            await self._memory.remember(seat, phase.value, summary)

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
