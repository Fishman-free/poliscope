"""Research task lifecycle: create, confirm claims, queue.

The service holds no state. It sequences the steps and enforces the one rule the
repository cannot see on its own: a task may not be queued until the researcher
has confirmed which atomic claims the council will investigate. CLAUDE.md 2
requires the researcher to direct the scope, and queueing before confirmation
would let the council pick its own question.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.epistemo.contracts import CouncilCheckpoint, TaskStatus
from packages.research.atomization import suggest_atomic_claims
from packages.research.contracts import ResearchContract
from packages.research.repository import (
    CLAIM_CONFIRMED,
    ResearchRepository,
    StoredClaim,
    StoredTask,
    TaskNotFound,
)


class UnconfirmedClaims(Exception):
    """Raised when queueing before atomic claims are confirmed."""


class InvalidPauseState(Exception):
    """Raised when pause/resume is attempted from a status that forbids it."""


class InvalidCouncilGuidanceState(Exception):
    """Raised when guidance is submitted outside AWAITING_COUNCIL_INPUT."""


@dataclass(frozen=True, slots=True)
class CreatedTask:
    task_id: UUID
    status: str
    suggested_claims: tuple[StoredClaim, ...]


class ResearchService:
    """Application service over :class:`ResearchRepository`."""

    def __init__(self, repository: ResearchRepository) -> None:
        self._repository = repository

    async def create(
        self,
        contract: ResearchContract,
        created_by: str = "api",
    ) -> CreatedTask:
        candidates = suggest_atomic_claims(contract)
        task_id = await self._repository.create_task(
            contract, candidates, created_by
        )
        return CreatedTask(
            task_id=task_id,
            status=TaskStatus.AWAITING_CLAIM_CONFIRMATION,
            suggested_claims=await self._repository.list_claims(task_id),
        )

    async def get_task(self, task_id: UUID) -> StoredTask:
        return await self._repository.get_task(task_id)

    async def suggested_claims(self, task_id: UUID) -> tuple[StoredClaim, ...]:
        await self._repository.get_task(task_id)
        return await self._repository.list_claims(task_id)

    async def confirm_claims(
        self,
        task_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> tuple[StoredClaim, ...]:
        await self._repository.get_task(task_id)
        return await self._repository.confirm_claims(task_id, claim_ids)

    async def queue(self, task_id: UUID) -> str:
        """Move a task to QUEUED once at least one claim is confirmed."""
        await self._repository.get_task(task_id)
        claims = await self._repository.list_claims(task_id)
        if not any(claim.status == CLAIM_CONFIRMED for claim in claims):
            raise UnconfirmedClaims(
                "atomic claims must be confirmed before queueing"
            )
        await self._repository.set_status(task_id, TaskStatus.QUEUED)
        return TaskStatus.QUEUED

    async def pause(self, task_id: UUID) -> str:
        """Move a QUEUED task to PAUSED so the worker never claims it.

        ``deliberate()`` always runs a task's full phase sequence in one
        uncommitted transaction (packages/epistemo/orchestrator.py), so there is
        no durable mid-run state today to snapshot and interrupt -- pausing a
        task that is already claimed and running would have nothing to stop.
        The honest, well-tested seam is upstream of that: ``claim_queued_tasks``
        (apps/worker/main.py) only ever selects ``TaskStatus.QUEUED`` rows, so a
        task moved to PAUSED before a worker claims it simply never gets run,
        with no orchestrator change required.
        """
        task = await self._repository.get_task(task_id)
        if task.status != TaskStatus.QUEUED:
            raise InvalidPauseState(
                f"task {task_id} is {task.status}, not {TaskStatus.QUEUED}; "
                "only a queued task can be paused"
            )
        await self._repository.set_status(task_id, TaskStatus.PAUSED)
        return TaskStatus.PAUSED

    async def add_pdf_object_id(self, task_id: UUID, object_id: UUID) -> None:
        """Record an uploaded PDF's object id against an already-created task."""
        await self._repository.get_task(task_id)
        await self._repository.add_pdf_object_id(task_id, object_id)

    async def resume(self, task_id: UUID) -> str:
        """Move a PAUSED task back to QUEUED so the worker can claim it again.

        Requeueing an already-run task is proven idempotent by
        ``test_replaying_a_requeued_task_duplicates_nothing`` -- every event's
        idempotency key is derived from phase and seat, so a resumed run
        replays as a no-op wherever it had already made progress.
        """
        task = await self._repository.get_task(task_id)
        if task.status != TaskStatus.PAUSED:
            raise InvalidPauseState(
                f"task {task_id} is {task.status}, not {TaskStatus.PAUSED}; "
                "only a paused task can be resumed"
            )
        await self._repository.set_status(task_id, TaskStatus.QUEUED)
        return TaskStatus.QUEUED

    async def submit_council_guidance(self, task_id: UUID, guidance_text: str) -> str:
        """Attach the human's advisory steer to the halted checkpoint and resume.

        Plan phase 8.2/8.3. ``guidance_text`` may be empty -- CLAUDE.md 4/8
        make this an advisory-only steer, never a vote, so "I choose not to
        intervene" is a complete, valid answer rather than a missing one. The
        checkpoint's other fields (the phases already run, carried state,
        absent seats, failures) are read back unchanged; only ``guidance`` is
        set, and the task is handed back to the worker as QUEUED so it can
        resume from JOINT_MODELING with this checkpoint.
        """
        task = await self._repository.get_task(task_id)
        if task.status != TaskStatus.AWAITING_COUNCIL_INPUT:
            raise InvalidCouncilGuidanceState(
                f"task {task_id} is {task.status}, not "
                f"{TaskStatus.AWAITING_COUNCIL_INPUT}; guidance can only be "
                "submitted while the council is halted at the checkpoint"
            )
        if task.council_checkpoint is None:
            # The status/checkpoint pair is set together by the worker
            # (apps/worker/jobs.py); seeing one without the other means the
            # invariant that guards this endpoint has already broken upstream,
            # not something a retry here can fix.
            raise InvalidCouncilGuidanceState(
                f"task {task_id} is {TaskStatus.AWAITING_COUNCIL_INPUT} but has "
                "no stored checkpoint to attach guidance to"
            )
        checkpoint_data = dict(task.council_checkpoint)
        checkpoint_data["guidance"] = guidance_text
        checkpoint = CouncilCheckpoint.model_validate(checkpoint_data)
        await self._repository.set_checkpoint(
            task_id, checkpoint.model_dump(mode="json")
        )
        await self._repository.set_status(task_id, TaskStatus.QUEUED)
        return TaskStatus.QUEUED


__all__ = [
    "CreatedTask",
    "InvalidCouncilGuidanceState",
    "InvalidPauseState",
    "ResearchService",
    "TaskNotFound",
    "UnconfirmedClaims",
]
