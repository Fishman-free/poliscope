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

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from packages.council.contracts import ALL_SEATS, Seat
from packages.council.rounds.registry import (
    PHASE_COMPLETED,
    PHASE_STARTED,
    EmittedEvent,
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
from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    CouncilCancelled,
    CouncilCheckpoint,
    CouncilPhaseSnapshot,
    TaskPhase,
    TaskStatus,
    checkpoint_failed_phases,
)
from packages.epistemo.state_machine import TaskStateMachine
from packages.epistemo.stopping import StopReason, decide_stop
from packages.evidence.lifecycle import QuarantinedNode
from packages.memory.collective import CollectiveMemory
from packages.memory.council_memory import CouncilMemory
from packages.memory.recall import get_policy, perspective_recall

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


def _render_role_context(role_context: object) -> str:
    """Flatten a perspective recall's RoleContext into one prompt-ready string.

    A seat's recall prompt is a single string; the role projection is a sorted
    list of the shared cognitive frontier. This renders it as "private recall
    (unchanged) + your role's evidence ranking", so the two never blur into one
    shared transcript.
    """
    process = getattr(role_context, "process_recall", "")
    projection = getattr(role_context, "evidence_projection", None)
    items = getattr(projection, "items", ())
    ranked: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        statement = item.get("statement")
        if statement:
            ranked.append(str(statement))
    if not ranked:
        return process
    ranked_text = "；".join(ranked)
    if process:
        return f"{process} [你的角色证据排序: {ranked_text}]"
    return f"[你的角色证据排序: {ranked_text}]"


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


# Per-seat run statuses, persisted to scientist_runs by the worker (round-9).
SEAT_RUN_COMPLETED = "completed"
SEAT_RUN_ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class SeatRunRecord:
    """One seat's participation in one phase, for the scientist_runs audit.

    The worker persists these to ``council_rounds`` / ``scientist_runs`` so a
    later researcher can reconstruct exactly who attended, how many times they
    were asked, and why an absent seat could not answer.
    """

    phase: TaskPhase
    seat: Seat
    status: str
    attempts: int
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
    # Per-seat attendance for the phases this pass actually ran (a resumed pass
    # only reports its own phases -- the worker persists each pass separately).
    # Empty when nothing ran (e.g. the whole run was skipped for budget).
    seat_runs: tuple[SeatRunRecord, ...] = ()
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
    seat_runs: list[SeatRunRecord] = field(default_factory=list)


def _episode_summary(
    phase: TaskPhase, events: list[EmittedEvent]
) -> str:
    """One seat's structured actions in a phase, compactly rendered.

    Upstream MemoBrain abstracts episodes into dependency-aware thoughts;
    feeding it "N events" would abstract nothing. Each of the seat's own
    events becomes one line -- event type plus the payload's salient text --
    capped per line so one wordy precommitment cannot drown the episode.
    """
    if not events:
        return ""
    lines = [f"{phase.value} round, {len(events)} structured actions:"]
    for event in events:
        payload = {key: value for key, value in event.payload.items() if key != "seat"}
        body = json.dumps(payload, ensure_ascii=False, default=str)
        if len(body) > 320:
            body = body[:320] + "…"
        lines.append(f"- {event.event_type}: {body}")
    return "\n".join(lines)


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
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        phases: tuple[TaskPhase, ...] | None = None,
        dialectical_fold: bool = True,
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
        # Evaluation-only protocol switches (design spec 11.4's ablation
        # ladder): ``phases`` runs a subset of the protocol (None = all eight,
        # which is every production caller), ``dialectical_fold`` turns the
        # JOINT_MODELING DebateCapsule into a plain fold that keeps no
        # opposition (True = the full system). Both default to the production
        # behaviour so existing callers are untouched.
        self._phases = phases
        self._dialectical_fold = dialectical_fold
        self._run_started: float | None = None
        # Collective executive memory: a materialised index over the ledger's
        # structured cognitive events, rebuilt from the events this run emits.
        # It feeds the seats' recall with the shared cognitive frontier (design
        # doc 1/6) while never writing the Evidence Graph itself.
        self._collective = CollectiveMemory()
        # Round-10 「停止研究」: polled between phases. When it returns True
        # the run halts at the next phase boundary with CANCELLED as the
        # terminal status, recording the phases it did complete. None when no
        # caller wired a cancel channel (unit tests, the evaluation harness).
        self._cancel_check = cancel_check

    async def run(
        self,
        task_id: UUID,
        question: str,
        confirmed_claims: tuple[UUID, ...] = (),
        claim_statements: Mapping[UUID, str] | None = None,
        quarantined: tuple[QuarantinedNode, ...] = (),
        pdf_object_ids: tuple[UUID, ...] = (),
        user_dois: tuple[str, ...] = (),
        knowledge_documents: tuple[KnowledgeDocumentLike, ...] = (),
        knowledge_search: KnowledgeSearcher | None = None,
        researcher_skills: tuple[tuple[str, str], ...] = (),
        output_language: str = "auto",
        paper_understanding: dict[str, object] | None = None,
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
        statements = dict(claim_statements or {})
        machine = TaskStateMachine()
        state = _Accumulator()
        report = TaskRunReport(task_id=task_id)
        run_phases: list[TaskPhase] = (
            list(resume_from.run_phases) if resume_from else []
        )
        kept: frozenset[TaskPhase] = (
            frozenset(resume_from.run_phases) if resume_from else frozenset()
        )
        skipped: list[TaskPhase] = []
        stop = StopReason.CONTINUE
        snapshots: list[CouncilPhaseSnapshot] = (
            list(resume_from.phase_snapshots) if resume_from else []
        )

        if resume_from is not None:
            restart = resume_from.restart_from
            if restart is not None:
                # Round-12 「重新研究从断点续跑」: the researcher chose to
                # restart from the first failed phase. Phases before it -- and
                # every phase after it that actually completed -- stay exactly
                # as the checkpoint recorded them (their events and carried
                # state are unchanged, so no ledger event is re-written and no
                # model budget is re-spent). Only the failed phase and any
                # later failed phase re-execute; the failed phase's own events
                # were never written when it failed, so the re-run writes them
                # for real -- this is the "unfinished phase is actually
                # redone" guarantee. A rewind needs the per-phase snapshots;
                # without them (a legacy checkpoint) we fall back to running
                # from the start, which is the honest no-state restart.
                failed = checkpoint_failed_phases(resume_from)
                restart_phases = {
                    phase
                    for phase in failed
                    if PHASE_SEQUENCE.index(phase)
                    >= PHASE_SEQUENCE.index(restart)
                }
                kept = frozenset(
                    phase
                    for phase in resume_from.run_phases
                    if phase not in restart_phases
                )
                snapshots = [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.phase in kept
                ]
                base: CouncilPhaseSnapshot | None = None
                for snapshot in snapshots:
                    if snapshot.phase in kept:
                        base = snapshot
                if base is not None:
                    state.carried.update(dict(base.carried))
                    state.unfilled.extend(base.unfilled)
                    state.absent.update(base.absent_seats)
                    state.failures.extend(base.failures)
                    state.events = base.events_appended
                else:
                    # No snapshot to rewind to: restart from scratch.
                    state = _Accumulator()
                    kept = frozenset()
                    snapshots = []
                run_phases = [phase for phase in PHASE_SEQUENCE if phase in kept]
            else:
                state.carried.update(dict(resume_from.carried))
                state.unfilled.extend(resume_from.unfilled)
                state.absent.update(resume_from.absent_seats)
                state.failures.extend(resume_from.failures)
                state.events = resume_from.events_appended
            # Fast-forward the state machine through every phase the original
            # run covered, in order -- including phases that failed and will
            # re-execute below -- so the strict "current + 1" rule holds when
            # the loop reaches the first phase after the checkpoint. A
            # restarted phase was part of the original run, so it is already
            # fast-forwarded past; executing it again needs no second
            # transition.
            for phase in resume_from.run_phases:
                machine.transition_to(phase)

        if self._memory is not None:
            await self._memory.open(self._seats, question)

        # Wall-clock enforcement starts here so a resumed run budgets only the
        # phases it still has left, not the ones already on the ledger.
        self._run_started = time.monotonic()

        phases = self._phases if self._phases is not None else PHASE_SEQUENCE
        for phase in PHASE_SEQUENCE:
            if phase not in phases:
                # Evaluation-only phase subset (design spec 11.4's
                # precommitment ablation): the state machine still advances
                # through the excluded phase -- its strict "current + 1"
                # rule holds -- but nothing runs and no event lands on the
                # ledger for it. Production callers never pass ``phases``.
                machine.transition_to(phase)
                continue
            if phase in kept:
                # Already-completed phases were fast-forwarded above and are
                # not executed again -- their events and carried state are
                # exactly what the checkpoint/snapshot recorded.
                continue
            if resume_from is None or phase not in resume_from.run_phases:
                # Advance the state machine only for phases the fast-forward
                # above did not already cover. A restarted phase (round-12)
                # was part of the original run, so it was fast-forwarded past;
                # re-executing it does not need a second transition.
                machine.transition_to(phase)
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
                report.seat_runs = tuple(state.seat_runs)
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
                    phase_snapshots=tuple(snapshots),
                )
                machine.transition_to(TaskStatus.AWAITING_COUNCIL_INPUT)
                return report
            # Round-10 「停止研究」: check the side channel at the phase
            # boundary. A stop lands between rounds, never mid-round, so the
            # phases that ran keep their events and the report says exactly
            # how far the run got before the researcher stopped it.
            if (
                self._cancel_check is not None
                and stop is StopReason.CONTINUE
                and await self._cancel_check()
            ):
                report.phases_run = tuple(run_phases)
                report.phases_skipped = tuple(skipped)
                report.events_appended = state.events
                report.unfilled_slots = tuple(state.unfilled)
                report.absent_seats = frozenset(state.absent)
                report.failures = tuple(state.failures)
                report.seat_runs = tuple(state.seat_runs)
                report.stop_reason = StopReason.CANCELLED
                report.final_status = TaskStatus.CANCELLED
                report.checkpoint = CouncilCheckpoint(
                    run_phases=tuple(run_phases),
                    carried=state.carried,
                    unfilled=tuple(state.unfilled),
                    absent_seats=tuple(
                        sorted(state.absent, key=lambda seat: seat.value)
                    ),
                    failures=tuple(state.failures),
                    events_appended=state.events,
                    phase_snapshots=tuple(snapshots),
                )
                machine.transition_to(TaskStatus.CANCELLED)
                return report
            if stop is StopReason.CONTINUE:
                stop = self._check_budget()
            if stop is not StopReason.CONTINUE:
                # A phase that never ran is a gap, not a silent omission.
                skipped.append(phase)
                state.unfilled.append(f"{phase.value}:not_reached")
                await self._append_skip(task_id, phase, stop)
                continue
            try:
                await self._run_phase(
                    task_id,
                    phase,
                    question,
                    confirmed_claims,
                    statements,
                    quarantined,
                    pdf_object_ids,
                    user_dois,
                    knowledge_documents,
                    knowledge_search,
                    researcher_skills,
                    output_language,
                    state,
                    council_guidance,
                    paper_understanding,
                )
            except CouncilCancelled:
                # Round-13 「停止研究」: the deliberator raised this from
                # inside an in-flight model call the researcher stopped. Same
                # CANCELLED contract as the between-phase check -- the phases
                # that ran keep their events, the report says exactly how far
                # the run got, and no PHASE_FAILED/unfilled slot is recorded
                # (a stop is a redirection, not a verdict on the work).
                report.phases_run = tuple(run_phases)
                report.phases_skipped = tuple(skipped)
                report.events_appended = state.events
                report.unfilled_slots = tuple(state.unfilled)
                report.absent_seats = frozenset(state.absent)
                report.failures = tuple(state.failures)
                report.seat_runs = tuple(state.seat_runs)
                report.stop_reason = StopReason.CANCELLED
                report.final_status = TaskStatus.CANCELLED
                report.checkpoint = CouncilCheckpoint(
                    run_phases=tuple(run_phases),
                    carried=state.carried,
                    unfilled=tuple(state.unfilled),
                    absent_seats=tuple(
                        sorted(state.absent, key=lambda seat: seat.value)
                    ),
                    failures=tuple(state.failures),
                    events_appended=state.events,
                    phase_snapshots=tuple(snapshots),
                )
                machine.transition_to(TaskStatus.CANCELLED)
                return report
            run_phases.append(phase)
            # One snapshot per phase that ran (successful or failed), with
            # the accumulated state after it -- the rewind material for
            # re-research (round-12).
            snapshots.append(
                CouncilPhaseSnapshot(
                    phase=phase,
                    carried=state.carried,
                    unfilled=tuple(state.unfilled),
                    absent_seats=tuple(
                        sorted(state.absent, key=lambda seat: seat.value)
                    ),
                    failures=tuple(state.failures),
                    events_appended=state.events,
                )
            )

        if state.absent:
            machine.transition_to(TaskStatus.DEGRADED_RUNNING)

        report.phases_run = tuple(run_phases)
        report.phases_skipped = tuple(skipped)
        report.events_appended = state.events
        report.unfilled_slots = tuple(state.unfilled)
        report.absent_seats = frozenset(state.absent)
        report.failures = tuple(state.failures)
        report.seat_runs = tuple(state.seat_runs)
        report.stop_reason = stop
        report.final_status = (
            TaskStatus.COMPLETED_WITH_GAPS if report.has_gaps else TaskStatus.COMPLETED
        )
        report.checkpoint = CouncilCheckpoint(
            run_phases=tuple(run_phases),
            carried=state.carried,
            unfilled=tuple(state.unfilled),
            absent_seats=tuple(sorted(state.absent, key=lambda seat: seat.value)),
            failures=tuple(state.failures),
            events_appended=state.events,
            phase_snapshots=tuple(snapshots),
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

        The wall-clock check runs first: it is the one budget dimension that
        had no enforcement (``consume_wall_clock`` was defined but never
        called), so a run whose model calls kept the worker alive could
        outlive its wall-clock budget by hours. Between phases is the natural
        cancellation point -- a phase is never interrupted mid-round, it
        simply does not start.
        """
        if self._run_started is not None:
            try:
                self._budget.record_elapsed(time.monotonic() - self._run_started)
            except BudgetExhausted:
                return StopReason.BUDGET_EXHAUSTED
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
        claim_statements: Mapping[UUID, str],
        quarantined: tuple[QuarantinedNode, ...],
        pdf_object_ids: tuple[UUID, ...],
        user_dois: tuple[str, ...],
        knowledge_documents: tuple[KnowledgeDocumentLike, ...],
        knowledge_search: KnowledgeSearcher | None,
        researcher_skills: tuple[tuple[str, str], ...],
        output_language: str,
        state: _Accumulator,
        council_guidance: str | None = None,
        paper_understanding: dict[str, object] | None = None,
    ) -> None:
        await self._ledger.append(
            task_id=task_id,
            event_type=PHASE_STARTED,
            payload={"phase": phase.value},
            idempotency_key=f"{phase.value}:started",
        )
        state.events += 1
        phase_started = datetime.now(UTC)

        context = PhaseContext(
            task_id=task_id,
            phase=phase,
            seats=self._seats,
            question=question,
            confirmed_claims=confirmed_claims,
            claim_statements=claim_statements,
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
            output_language=output_language,
            guidance=council_guidance,
            paper_understanding=paper_understanding,
            dialectical_fold=self._dialectical_fold,
            collective=self._collective.view().summary,
        )
        try:
            outcome = await runner_for(phase)(context)
        except CouncilCancelled:
            # Round-13 「停止研究」: the researcher's stop request landed
            # mid-call (the deliberator polls the cancel channel around every
            # model call). Stopping is a redirection, not a failure -- neither
            # a PHASE_FAILED event nor an unfilled slot belongs on the ledger
            # for a run the researcher chose to end. Propagate to run(), which
            # records the CANCELLED report.
            raise
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
        # Per-seat attendance for the scientist_runs audit (round-9): every seat
        # in this phase is recorded once, with how many attempts it took and --
        # for an absent one -- the final honest error. A whole-phase failure
        # (the except branch above) records nothing, because the ledger already
        # carries PHASE_FAILED and there is no per-seat detail to report.
        #
        # REPORTING is skipped here: it assembles the report from the graph and
        # never polls a seat, so its ``outcome.attempts`` is empty -- writing a
        # row would falsely claim seven scientists attended the synthesis.
        # Guarding on ``outcome.attempts`` rather than the phase name keeps this
        # honest if a future phase stops polling seats too.
        if outcome.attempts:
            phase_completed = datetime.now(UTC)
            for seat in self._seats:
                absent_seat = seat in outcome.absent_seats
                state.seat_runs.append(
                    SeatRunRecord(
                        phase=phase,
                        seat=seat,
                        status=(
                            SEAT_RUN_ABSENT if absent_seat else SEAT_RUN_COMPLETED
                        ),
                        attempts=outcome.attempts.get(seat, 0),
                        error_code=(
                            outcome.absence_reasons.get(seat)
                            if absent_seat
                            else None
                        ),
                        started_at=phase_started,
                        completed_at=phase_completed,
                    )
                )
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
        private = await self._memory.recall(self._seats)
        # Perspective recall (design doc 7): each seat sees its own private
        # process recall PLUS a role-ranked projection of the shared cognitive
        # frontier, so the causal scientist meets causal claims first and the
        # measurement scientist meets measurement claims first -- one fact base,
        # seven cognitive cuts. The projection is a *summary* of the shared
        # frontier, appended to (never merged into) the seat's private recall.
        snapshot = self._collective.evidence_snapshot()
        if not snapshot:
            return private
        projected: dict[Seat, str] = {}
        for seat in self._seats:
            policy = get_policy(seat.value)
            role_context = perspective_recall(
                policy, private.get(seat, ""), snapshot
            )
            projected[seat] = _render_role_context(role_context)
        return projected

    async def _remember(self, phase: TaskPhase, outcome: PhaseOutcome) -> None:
        """Record what the round did in each participating seat's own memory.

        Round-16: the episode fed to upstream MemoBrain is the seat's own
        structured actions in this phase (event types plus their payloads),
        not a bare event count -- upstream thought construction abstracts the
        semantic content, and "N events" would abstract nothing.

        An absent seat gets nothing: writing "you were unavailable" into its
        private memory would let a later round mistake the record of a gap for
        the seat's own reasoning. The gap is already on the ledger, where the
        researcher can see it.
        """
        if self._memory is None:
            return
        per_seat: dict[Seat, list[EmittedEvent]] = {
            seat: [] for seat in self._seats
        }
        for event in outcome.events:
            seat_value = event.payload.get("seat")
            if isinstance(seat_value, str):
                try:
                    event_seat = Seat(seat_value)
                except ValueError:
                    continue
                # An ablation run's ablated seat is not in self._seats; its
                # leftover events must not resurrect a memory slot for it.
                if event_seat in per_seat:
                    per_seat[event_seat].append(event)
        for seat in self._seats:
            if seat in outcome.absent_seats:
                continue
            summary = _episode_summary(phase, per_seat.get(seat, []))
            if not summary:
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
        # Feed the collective executive memory from the same structured events
        # the ledger just received -- it is a materialised view, never a second
        # source of truth (CLAUDE.md 6). Only the emitted structured actions
        # reach it, never a seat's private reasoning (CLAUDE.md 3/11).
        self._collective.absorb(outcome.events)


__all__ = [
    "PHASE_FAILED",
    "PHASE_SKIPPED",
    "CouncilOrchestrator",
    "EventSink",
    "RoundResult",
    "TaskRunReport",
    "run_round",
]
