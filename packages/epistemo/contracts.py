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
