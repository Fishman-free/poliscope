"""Persistence for research tasks, scopes, and atomic claims.

The service layer above holds no state of its own. Every fact about a task lives
in PostgreSQL, because CLAUDE.md 8 makes the database the source of truth and
CLAUDE.md 10 requires a task to be resumable after the process that created it is
gone.

This repository owns three tables and nothing else. It does not touch the event
ledger or the evidence graph: a research task is created and its claims are
confirmed before any evidence exists, so mixing the two would let a caller write
graph state through the wrong door.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.epistemo.contracts import TaskStatus
from packages.research.atomization import AtomicClaimCandidate
from packages.research.contracts import ResearchContract
from packages.research.models import (
    AtomicClaimModel,
    ResearchScopeModel,
    ResearchTaskModel,
)

CLAIM_SUGGESTED = "SUGGESTED"
CLAIM_CONFIRMED = "CONFIRMED"
CLAIM_DISCARDED = "DISCARDED"


@dataclass(frozen=True, slots=True)
class StoredClaim:
    claim_id: UUID
    statement: str
    claim_type: str
    scope: dict[str, object]
    falsification_condition: str
    status: str


@dataclass(frozen=True, slots=True)
class StoredTask:
    task_id: UUID
    question: str
    status: str
    created_by: str
    # Plan phase 8.2: the serialized CouncilCheckpoint (packages/epistemo/
    # contracts.py), non-None only while status is AWAITING_COUNCIL_INPUT.
    # Exposed here rather than via a separate read method because every
    # caller that needs the checkpoint already calls get_task() first to
    # check status -- a second round trip would just re-fetch the same row.
    council_checkpoint: dict[str, Any] | None = None
    # Knowledge base linked at creation (migration 0010), None when the task
    # retrieves from the open web only. The worker reads this to feed the
    # council the researcher's own documents.
    knowledge_base_id: UUID | None = None
    # Creation timestamp, filled by get_task and list_tasks -- the web
    # session-history panel sorts by it.
    created_at: datetime | None = None
    # Owning account (migration 0012); isolation queries filter on it.
    user_id: UUID | None = None


class TaskNotFound(Exception):
    """Raised when a task id does not exist.

    Distinct from a validation error so the API can answer 404 rather than 400.
    """


class ResearchRepository:
    """One instance per request, wrapping one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_task(
        self,
        contract: ResearchContract,
        candidates: tuple[AtomicClaimCandidate, ...],
        created_by: str,
        user_id: UUID | None = None,
    ) -> UUID:
        """Persist a contract and its suggested claims in one transaction.

        The task lands in AWAITING_CLAIM_CONFIRMATION rather than QUEUED: the
        researcher directs the scope, so nothing starts researching until the
        claims are confirmed.
        """
        task_id = uuid4()
        self._session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=contract.question,
                status=TaskStatus.AWAITING_CLAIM_CONFIRMATION,
                created_by=created_by,
                user_id=user_id,
                skill_ids=list(contract.skill_ids),
                wall_clock_minutes=contract.budget.wall_clock_minutes,
                model_cost_usd=contract.budget.model_cost_usd,
                tool_call_limit=contract.budget.tool_call_limit,
                source_limit=contract.budget.source_limit,
                user_evidence=contract.user_evidence.model_dump(mode="json"),
                model_config=(
                    contract.task_model_config.model_dump(mode="json")
                    if contract.task_model_config is not None
                    else None
                ),
                knowledge_base_id=contract.knowledge_base_id,
            )
        )
        # Flushed before the dependent rows because their foreign key targets
        # research_tasks.task_id, a unique column rather than the primary key.
        # SQLAlchemy's unit of work orders inserts by primary key dependency, so
        # without this the claims can reach the database first.
        await self._session.flush()
        scope = contract.scope
        self._session.add(
            ResearchScopeModel(
                id=uuid4(),
                task_id=task_id,
                status=TaskStatus.AWAITING_CLAIM_CONFIRMATION,
                created_by=created_by,
                populations=list(scope.populations),
                regions=list(scope.regions),
                languages=list(scope.languages),
                date_from=scope.date_from,
                date_until=scope.date_until,
                evidence_priorities=[str(p) for p in scope.evidence_priorities],
                allow_preprints=scope.allow_preprints,
            )
        )
        for candidate in candidates:
            self._session.add(
                AtomicClaimModel(
                    id=candidate.claim_id,
                    task_id=task_id,
                    statement=candidate.statement,
                    claim_type=str(candidate.claim_type),
                    scope=dict(candidate.scope),
                    falsification_condition=candidate.falsification_condition,
                    status=CLAIM_SUGGESTED,
                    created_by=created_by,
                )
            )
        await self._session.flush()
        return task_id

    async def get_task(
        self, task_id: UUID, user_id: UUID | None = None
    ) -> StoredTask:
        """Fetch one task, optionally scoped to an owning account.

        With ``user_id`` given, a task owned by someone else (or by no one --
        pre-account rows) reads as TaskNotFound: the existence of another
        account's task must not leak through a 404-vs-403 distinction.
        """
        query = select(ResearchTaskModel).where(
            ResearchTaskModel.task_id == task_id
        )
        if user_id is not None:
            query = query.where(ResearchTaskModel.user_id == user_id)
        row = await self._session.scalar(query)
        if row is None:
            raise TaskNotFound(str(task_id))
        return StoredTask(
            task_id=row.task_id,
            question=row.question,
            status=row.status,
            created_by=row.created_by,
            council_checkpoint=row.council_checkpoint,
            knowledge_base_id=row.knowledge_base_id,
            created_at=row.created_at,
            user_id=row.user_id,
        )

    async def list_tasks(
        self, user_id: UUID, limit: int = 50
    ) -> tuple[StoredTask, ...]:
        """Most-recent-first task summaries for one account's session history.

        Newest on top (``created_at`` has an index; ``task_id`` breaks ties
        deterministically). The list intentionally stays lightweight -- no
        claims, no evidence -- because the panel only needs enough to label
        and jump to a session. Pre-account rows (``user_id`` NULL) belong to
        no one and appear in no one's history.
        """
        result = await self._session.execute(
            select(ResearchTaskModel)
            .where(ResearchTaskModel.user_id == user_id)
            .order_by(
                ResearchTaskModel.created_at.desc(),
                ResearchTaskModel.task_id.desc(),
            )
            .limit(limit)
        )
        return tuple(
            StoredTask(
                task_id=row.task_id,
                question=row.question,
                status=row.status,
                created_by=row.created_by,
                council_checkpoint=row.council_checkpoint,
                knowledge_base_id=row.knowledge_base_id,
                created_at=row.created_at,
                user_id=row.user_id,
            )
            for row in result.scalars()
        )

    async def list_claims(self, task_id: UUID) -> tuple[StoredClaim, ...]:
        result = await self._session.execute(
            select(AtomicClaimModel)
            .where(AtomicClaimModel.task_id == task_id)
            .order_by(AtomicClaimModel.created_at, AtomicClaimModel.id)
        )
        return tuple(
            StoredClaim(
                claim_id=row.id,
                statement=row.statement,
                claim_type=row.claim_type,
                scope=dict(row.scope),
                falsification_condition=row.falsification_condition,
                status=row.status,
            )
            for row in result.scalars()
        )

    async def confirm_claims(
        self,
        task_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> tuple[StoredClaim, ...]:
        """Mark the chosen claims confirmed and the rest discarded.

        Discarded rather than deleted: CLAUDE.md 5.3 forbids physically removing
        anything the council once considered, so a claim the researcher rejected
        stays visible in the audit trail.
        """
        known = {claim.claim_id for claim in await self.list_claims(task_id)}
        if not known:
            raise TaskNotFound(str(task_id))
        unknown = set(claim_ids) - known
        if unknown:
            raise ValueError(
                f"claims do not belong to task {task_id}: {sorted(map(str, unknown))}"
            )
        if not claim_ids:
            raise ValueError("at least one atomic claim must be confirmed")
        await self._session.execute(
            update(AtomicClaimModel)
            .where(AtomicClaimModel.task_id == task_id)
            .values(status=CLAIM_DISCARDED)
        )
        await self._session.execute(
            update(AtomicClaimModel)
            .where(
                AtomicClaimModel.task_id == task_id,
                AtomicClaimModel.id.in_(claim_ids),
            )
            .values(status=CLAIM_CONFIRMED)
        )
        await self._session.flush()
        return await self.list_claims(task_id)

    async def set_status(self, task_id: UUID, status: TaskStatus) -> None:
        # An UPDATE returns a CursorResult, which is where rowcount lives; the
        # count is what distinguishes "set it" from "no such task". The cast is
        # needed because execute() is typed as returning the Result base class.
        result = cast(CursorResult[Any], await self._session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(status=status)
        ))
        if result.rowcount == 0:
            raise TaskNotFound(str(task_id))
        await self._session.flush()

    async def set_checkpoint(
        self,
        task_id: UUID,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        """Persist (or clear) the serialized CouncilCheckpoint column.

        Plan phase 8.2: the worker writes a checkpoint when a run halts at
        AWAITING_COUNCIL_INPUT, and clears it (passes None) once the task
        resumes past JOINT_MODELING -- a completed task has nothing left to
        resume from, and leaving stale JSON behind would let a future reader
        mistake it for a still-pending checkpoint.
        """
        result = cast(CursorResult[Any], await self._session.execute(
            update(ResearchTaskModel)
            .where(ResearchTaskModel.task_id == task_id)
            .values(council_checkpoint=checkpoint)
        ))
        if result.rowcount == 0:
            raise TaskNotFound(str(task_id))
        await self._session.flush()

    async def add_pdf_object_id(self, task_id: UUID, object_id: UUID) -> None:
        """Append an uploaded PDF's object id to the task's stored evidence.

        A task must exist before an object can reference it (``objects.task_id``
        is a NOT NULL foreign key), so an uploaded PDF cannot ride in on
        ``ResearchContract.user_evidence`` at creation time the way a DOI or
        BibTeX entry does. This patches the already-created task's
        ``user_evidence`` JSONB after the fact, before ``confirm_claims``/
        ``queue`` moves it out of AWAITING_CLAIM_CONFIRMATION.

        Reassigning ``row.user_evidence`` to a new dict (rather than mutating
        the existing one in place) is what makes SQLAlchemy notice the change
        on a plain JSONB column with no change-tracking wrapper.
        """
        row = await self._session.scalar(
            select(ResearchTaskModel).where(ResearchTaskModel.task_id == task_id)
        )
        if row is None:
            raise TaskNotFound(str(task_id))
        current = dict(row.user_evidence)
        existing_ids = list(current.get("pdf_object_ids", ()))
        object_id_str = str(object_id)
        if object_id_str not in existing_ids:
            existing_ids.append(object_id_str)
        current["pdf_object_ids"] = existing_ids
        row.user_evidence = current
        await self._session.flush()
