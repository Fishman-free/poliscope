from __future__ import annotations

from enum import StrEnum

from packages.council.contracts import Seat
from packages.kernel.contracts import ContractModel, FrozenDict


class TaskStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_CLAIM_CONFIRMATION = "AWAITING_CLAIM_CONFIRMATION"
    QUEUED = "QUEUED"
    # The database-level "claimed and being run" state, set by the worker's
    # claim transaction and cleared by the run's terminal status. Distinct
    # from DEGRADED_RUNNING (a no-persistence state-machine marker for runs
    # with absent seats): RUNNING exists so a second worker (or the same
    # worker's next poll) cannot claim a task that is already running --
    # without it, two parallel runs of one task collide on idempotency keys
    # and the task dies with EventConflict. Crashed workers leave stale
    # RUNNING rows; the worker reclaims them (see recover_stale_running).
    RUNNING = "RUNNING"
    DEGRADED_RUNNING = "DEGRADED_RUNNING"
    # Plan phase 8: the one fixed checkpoint between BLINDSPOT_BOUNTY and
    # JOINT_MODELING. Not a general pause (CLAUDE.md 17 deviation, recorded in
    # the plan) -- the council halts here so a human can give a directional
    # steer (CLAUDE.md 4/8: advisory only, never a vote that decides scientific
    # truth) before joint modeling starts.
    AWAITING_COUNCIL_INPUT = "AWAITING_COUNCIL_INPUT"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_GAPS = "COMPLETED_WITH_GAPS"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    # The researcher stopped the run on purpose (round-10 「停止研究」). A
    # terminal status like the others, so the report shows what ran before the
    # stop and the worker never re-claims it. Distinct from FAILED: nothing
    # broke, the human redirected.
    CANCELLED = "CANCELLED"


class TaskPhase(StrEnum):
    PRECOMMITMENT = "PRECOMMITMENT"
    ACQUISITION = "ACQUISITION"
    EVIDENCE_EXCHANGE = "EVIDENCE_EXCHANGE"
    CROSS_EXAMINATION = "CROSS_EXAMINATION"
    BLINDSPOT_BOUNTY = "BLINDSPOT_BOUNTY"
    JOINT_MODELING = "JOINT_MODELING"
    FINAL_REJUDGMENT = "FINAL_REJUDGMENT"
    REPORTING = "REPORTING"


TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)

PHASE_SEQUENCE: tuple[TaskPhase, ...] = (
    TaskPhase.PRECOMMITMENT,
    TaskPhase.ACQUISITION,
    TaskPhase.EVIDENCE_EXCHANGE,
    TaskPhase.CROSS_EXAMINATION,
    TaskPhase.BLINDSPOT_BOUNTY,
    TaskPhase.JOINT_MODELING,
    TaskPhase.FINAL_REJUDGMENT,
    TaskPhase.REPORTING,
)

PHASE_TO_STATUS: dict[TaskPhase, TaskStatus] = {
    TaskPhase.PRECOMMITMENT: TaskStatus.QUEUED,
    TaskPhase.ACQUISITION: TaskStatus.QUEUED,
    TaskPhase.EVIDENCE_EXCHANGE: TaskStatus.QUEUED,
    TaskPhase.CROSS_EXAMINATION: TaskStatus.QUEUED,
    TaskPhase.BLINDSPOT_BOUNTY: TaskStatus.QUEUED,
    TaskPhase.JOINT_MODELING: TaskStatus.QUEUED,
    TaskPhase.FINAL_REJUDGMENT: TaskStatus.QUEUED,
    TaskPhase.REPORTING: TaskStatus.REPORTING,
}


class CouncilCheckpoint(ContractModel):
    """Serializable resume state for the BLINDSPOT_BOUNTY -> JOINT_MODELING gate.

    ``CouncilOrchestrator.run()`` holds no state between calls (see its
    docstring) -- resume today works only because every ledger append is
    idempotent, so a full replay just no-ops the phases already written. That
    is too wasteful to use for a routine, expected-every-time human checkpoint:
    it would re-run seat deliberation and re-spend budget bookkeeping for five
    already-completed phases on every single task. This contract is what lets
    a second ``run()`` call skip straight to JOINT_MODELING instead, carrying
    forward exactly the fields ``_Accumulator`` tracks -- nothing more, since
    CLAUDE.md 10 requires this state to be exactly what was really produced,
    not a guess at what resuming should look like.
    """

    run_phases: tuple[TaskPhase, ...] = ()
    carried: FrozenDict[str, object] = FrozenDict()
    unfilled: tuple[str, ...] = ()
    absent_seats: tuple[Seat, ...] = ()
    failures: tuple[str, ...] = ()
    events_appended: int = 0
    # Round-12 「重新研究从断点续跑」: one snapshot per phase that ran (in
    # phase order), recording the accumulated state *after* that phase. Lets a
    # re-research run rewind to the first failed phase instead of resuming
    # from the end of the checkpoint -- the failed phase (and any later failed
    # phase) re-executes, while every phase that actually completed stays
    # exactly as it was (its events and carried state are unchanged, so no
    # ledger event is re-written and no model budget is re-spent on it).
    phase_snapshots: tuple[CouncilPhaseSnapshot, ...] = ()
    # Round-12: set by POST /re-research with mode=first_gap when the
    # checkpoint's first failed phase was found. The orchestrator rewinds to
    # that phase; None means "resume from the end of the checkpoint".
    restart_from: TaskPhase | None = None
    # Plan phase 8.3: the human's advisory directional steer, set by
    # POST /api/tasks/{id}/council-guidance while status is
    # AWAITING_COUNCIL_INPUT. None before the human has responded; "" is a
    # deliberate, explicit "no intervention, just continue" (CLAUDE.md 4/8 --
    # this is never a vote, so an empty string is a valid, honest answer, not
    # a missing one). Rendered into the JOINT_MODELING prompt only -- see
    # packages/council/deliberation.py::_user_prompt -- and never read by any
    # Evidence Gate stage, Claim adoption path, or DissentCertificate/
    # DebateCapsule construction.
    guidance: str | None = None


class CouncilPhaseSnapshot(ContractModel):
    """The accumulated run state immediately after one phase completed.

    ``run()`` appends one snapshot per phase it ran (successful or failed),
    so the re-research rewind in ``run(restart_from=...)`` can reconstruct
    the exact state before the first failed phase without re-running any of
    the phases that came before it.
    """

    phase: TaskPhase
    carried: FrozenDict[str, object] = FrozenDict()
    unfilled: tuple[str, ...] = ()
    absent_seats: tuple[Seat, ...] = ()
    failures: tuple[str, ...] = ()
    events_appended: int = 0


def checkpoint_failed_phases(
    checkpoint: CouncilCheckpoint,
) -> frozenset[TaskPhase]:
    """The phases a checkpoint records as failed or skipped.

    A phase counts as not-complete when its runner raised (recorded in
    ``failures`` and as a ``{phase}:round_failed`` slot) or was skipped for
    budget (``{phase}:not_reached``). Ordinary evidence gaps -- e.g.
    ``ACQUISITION:no_finding:...`` -- are not failures: they are honest holes
    in the evidence, which is exactly what COMPLETED_WITH_GAPS reports.
    """
    failed: set[TaskPhase] = set()
    for failure in checkpoint.failures:
        for phase in PHASE_SEQUENCE:
            if failure.startswith(f"{phase.value}:"):
                failed.add(phase)
    for slot in checkpoint.unfilled:
        for phase in PHASE_SEQUENCE:
            if slot.startswith(f"{phase.value}:round_failed") or slot.startswith(
                f"{phase.value}:not_reached"
            ):
                failed.add(phase)
    return frozenset(failed)


def first_unfinished_phase(
    checkpoint: CouncilCheckpoint,
) -> TaskPhase | None:
    """The earliest phase recorded as failed/skipped, in protocol order."""
    failed = checkpoint_failed_phases(checkpoint)
    for phase in PHASE_SEQUENCE:
        if phase in failed:
            return phase
    return None
