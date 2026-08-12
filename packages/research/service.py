"""Research task lifecycle: create, confirm claims, queue.

The service holds no state. It sequences the steps and enforces the one rule the
repository cannot see on its own: a task may not be queued until the researcher
has confirmed which atomic claims the council will investigate. CLAUDE.md 2
requires the researcher to direct the scope, and queueing before confirmation
would let the council pick its own question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from packages.epistemo.contracts import (
    CouncilCheckpoint,
    TaskStatus,
    first_unfinished_phase,
)
from packages.evidence.contracts import ClaimType
from packages.research.atomization import (
    AtomicClaimCandidate,
    suggest_atomic_claims,
)
from packages.research.contracts import (
    ResearchBudget,
    ResearchContract,
    ResearchScope,
    TaskModelConfig,
    UserEvidenceInput,
)
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
        user_id: UUID | None = None,
    ) -> CreatedTask:
        candidates = suggest_atomic_claims(contract)
        task_id = await self._repository.create_task(
            contract, candidates, created_by, user_id=user_id
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

    async def re_research(self, task_id: UUID, mode: str = "first_gap") -> str:
        """Move a FAILED (or CANCELLED) task back to QUEUED for another run.

        ``重新研究`` (round-8): a task that ended in FAILED -- e.g. an
        unrecoverable event conflict or a watchdog timeout -- is requeued.
        Round-10: a CANCELLED task (the researcher stopped it) is re-runnable
        the same way -- stopping was a redirection, not a verdict on the work.

        Round-12 「重新研究模式」: ``mode`` decides where the re-run starts.

        - ``full``: the council checkpoint is cleared, so the worker re-runs
          the whole protocol from PRECOMMITMENT. Ledger idempotency keys make
          the replay safe (completed phases' events no-op), but every seat is
          asked again and the process stream shows the full run.
        - ``first_gap`` (default): if the stored checkpoint records a failed
          or skipped phase, the checkpoint is marked ``restart_from`` so the
          worker rewinds to that first unfinished phase -- it re-executes
          (its events were never written), while every completed phase stays
          exactly as it was. If there is no gap (or no checkpoint), this
          degrades to ``full``: starting over from the beginning.
        """
        task = await self._repository.get_task(task_id)
        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise InvalidPauseState(
                f"task {task_id} is {task.status}, not {TaskStatus.FAILED} or "
                f"{TaskStatus.CANCELLED}; only a failed or cancelled task can "
                "be re-researched"
            )
        if mode != "full" and task.council_checkpoint is not None:
            # first_gap: rewind to the first unfinished phase when one is
            # recorded; otherwise fall through to the full restart below.
            checkpoint = CouncilCheckpoint.model_validate(
                task.council_checkpoint
            )
            first = first_unfinished_phase(checkpoint)
            if first is not None:
                # The contract is immutable; rebuild the checkpoint with
                # the restart marker so the worker rewinds to that phase.
                checkpoint = CouncilCheckpoint(
                    run_phases=checkpoint.run_phases,
                    carried=checkpoint.carried,
                    unfilled=checkpoint.unfilled,
                    absent_seats=checkpoint.absent_seats,
                    failures=checkpoint.failures,
                    events_appended=checkpoint.events_appended,
                    phase_snapshots=checkpoint.phase_snapshots,
                    restart_from=first,
                    guidance=checkpoint.guidance,
                )
                await self._repository.set_checkpoint(
                    task_id, checkpoint.model_dump(mode="json")
                )
                await self._repository.set_status(
                    task_id, TaskStatus.QUEUED
                )
                return TaskStatus.QUEUED
        # full, or first_gap with no recorded gap: clear the checkpoint and
        # start over from the beginning.
        await self._repository.set_checkpoint(task_id, None)
        await self._repository.set_status(task_id, TaskStatus.QUEUED)
        return TaskStatus.QUEUED

    async def rerun_fresh(
        self,
        task_id: UUID,
        created_by: str,
        user_id: UUID | None = None,
    ) -> UUID:
        """「从头研究」(round-13): start a brand-new task from PRECOMMITMENT.

        Re-running *the same task* from the start cannot be a true restart:
        the ledger's idempotency keys are derived from phase and seat, not
        from the run, so a fresh pass's events would be swallowed as no-ops by
        the previous run's rows -- the council would be re-polled and the
        researcher would still see the old round's evidence. The only honest
        "start over" is a new task: fresh ledger, fresh evidence graph, fresh
        process stream, same question, same scope, same confirmed atomic
        claims (copied with new ids), same budget and model configuration.
        The original task is left untouched as audit history.

        The fresh task goes straight to QUEUED -- the researcher already
        confirmed these claims once; re-confirming a copy would be ceremony
        without a decision.
        """
        task = await self._repository.get_task(task_id, user_id=user_id)
        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise InvalidPauseState(
                f"task {task_id} is {task.status}, not {TaskStatus.FAILED} or "
                f"{TaskStatus.CANCELLED}; a fresh rerun starts from a task "
                "that ended in failure or was stopped"
            )
        scope = await self._repository.get_scope(task_id)
        if scope is None:
            # Legacy row with no research_scopes entry: a permissive default
            # rather than inventing a scope the researcher never set.
            scope = ResearchScope(
                populations=(),
                regions=(),
                languages=(),
                date_from=None,
                date_until=date.today(),
                evidence_priorities=(),
                allow_preprints=True,
            )
        contract = ResearchContract(
            question=task.question,
            scope=scope,
            budget=ResearchBudget(
                wall_clock_minutes=task.wall_clock_minutes,
                model_cost_usd=Decimal(task.model_cost_usd),
                tool_call_limit=task.tool_call_limit,
                source_limit=task.source_limit,
            ),
            user_evidence=UserEvidenceInput.model_validate(task.user_evidence or {}),
            task_model_config=(
                TaskModelConfig.model_validate(task.model_config)
                if task.model_config is not None
                else None
            ),
            knowledge_base_id=task.knowledge_base_id,
            skill_ids=task.skill_ids,
            output_language=task.output_language,
            task_type=task.task_type,
        )
        claims = await self._repository.list_claims(task_id)
        confirmed = tuple(c for c in claims if c.status == CLAIM_CONFIRMED)
        if not confirmed:
            raise UnconfirmedClaims(
                "the source task has no confirmed atomic claims to rerun"
            )
        candidates = tuple(
            AtomicClaimCandidate(
                claim_id=uuid4(),
                statement=claim.statement,
                claim_type=ClaimType(claim.claim_type),
                scope={key: str(value) for key, value in claim.scope.items()},
                falsification_condition=claim.falsification_condition,
            )
            for claim in confirmed
        )
        fresh_id = await self._repository.create_task(
            contract, candidates, created_by, user_id=user_id
        )
        await self._repository.confirm_claims(
            fresh_id, tuple(candidate.claim_id for candidate in candidates)
        )
        await self._repository.set_status(fresh_id, TaskStatus.QUEUED)
        return fresh_id

    async def cancel(self, task_id: UUID, requested_by: str = "researcher") -> str:
        """Stop a task: QUEUED/PAUSED directly, RUNNING via the side channel.

        ``停止研究`` (round-10). A QUEUED or PAUSED task is flipped to CANCELLED
        right here -- nothing holds its row. A RUNNING (or checkpoint-halted)
        task cannot be flipped (the worker holds the row locked while it runs),
        so a cancel request is recorded in ``task_cancel_requests``; the worker
        polls it between phases and halts the run early with CANCELLED as the
        terminal status. Either way the caller gets a terminal status and the
        task is never re-claimed.
        """
        task = await self._repository.get_task(task_id)
        if task.status in (TaskStatus.COMPLETED, TaskStatus.COMPLETED_WITH_GAPS,
                           TaskStatus.FAILED, TaskStatus.CANCELLED):
            # Already terminal: nothing to stop, report the fact honestly
            # rather than pretending a new stop happened.
            return task.status
        if task.status in (TaskStatus.QUEUED, TaskStatus.PAUSED,
                           TaskStatus.AWAITING_COUNCIL_INPUT):
            # Nothing holds these rows: QUEUED/PAUSED wait for a worker,
            # AWAITING_COUNCIL_INPUT sits parked at the checkpoint (the
            # worker's transaction already committed and released the lock).
            # All three can be flipped straight to CANCELLED.
            await self._repository.set_status(task_id, TaskStatus.CANCELLED)
            return TaskStatus.CANCELLED
        if task.status in (TaskStatus.RUNNING, TaskStatus.DEGRADED_RUNNING,
                           TaskStatus.REPORTING):
            # A worker is actively running this task and holds the row locked,
            # so the API cannot flip the status directly. The stop is recorded
            # in the side channel and the worker's between-phase poll halts the
            # run with CANCELLED.
            await self._repository.request_cancel(task_id, requested_by)
            return TaskStatus.CANCELLED
        # AWAITING_CLAIM_CONFIRMATION / DRAFT: never queued, never running --
        # the researcher is still shaping it. Cancel is meaningless; deleting
        # the draft is the honest action, so report it back as-is.
        return task.status

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
