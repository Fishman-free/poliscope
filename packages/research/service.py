from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.research.atomization import (
    AtomicClaimCandidate,
    suggest_atomic_claims,
)
from packages.research.contracts import ResearchContract


class UnconfirmedClaims(Exception):
    """Raised when queueing before atomic claims are confirmed."""


@dataclass(frozen=True, slots=True)
class ResearchTask:
    id: UUID
    contract: ResearchContract
    status: str
    confirmed_claims: frozenset[UUID] = frozenset()


@dataclass
class ResearchService:
    _tasks: dict[UUID, ResearchTask] = field(default_factory=dict)
    _confirmed: dict[UUID, frozenset[UUID]] = field(default_factory=dict)
    _suggestions: dict[UUID, tuple[AtomicClaimCandidate, ...]] = field(
        default_factory=dict
    )

    async def create(self, contract: ResearchContract) -> ResearchTask:
        task_id = uuid4()
        task = ResearchTask(id=task_id, contract=contract, status="DRAFT")
        self._tasks[task_id] = task
        self._suggestions[task_id] = suggest_atomic_claims(contract)
        return task

    def suggest_atomic_claims(
        self, task_id: UUID
    ) -> tuple[AtomicClaimCandidate, ...]:
        return self._suggestions.get(task_id, ())

    async def confirm_claims(
        self, task_id: UUID, claim_ids: list[UUID]
    ) -> bool:
        self._confirmed[task_id] = frozenset(claim_ids)
        return True

    async def queue(self, task_id: UUID) -> ResearchTask:
        confirmed = self._confirmed.get(task_id, frozenset())
        if not confirmed:
            raise UnconfirmedClaims(
                "atomic claims must be confirmed before queueing"
            )
        task = self._tasks[task_id]
        updated = ResearchTask(
            id=task.id,
            contract=task.contract,
            status="QUEUED",
            confirmed_claims=confirmed,
        )
        self._tasks[task_id] = updated
        return updated
