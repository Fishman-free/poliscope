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

from packages.epistemo.contracts import TaskStatus
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


__all__ = [
    "CreatedTask",
    "InvalidPauseState",
    "ResearchService",
    "TaskNotFound",
    "UnconfirmedClaims",
]
