"""One unit of background work: run a queued task through the protocol.

The job is split into two transactions on purpose, and the split is the whole
point of the module.

1. **Deliberation** runs as ``poliscope_app``. It appends to the Scientific Event
   Ledger and updates the task row. That identity holds no write privilege on
   ``graph_nodes`` or ``graph_edges``, so a bug here cannot reach the Evidence
   Graph even if it tries.
2. **Projection** runs as ``poliscope_projector``. It reads the committed ledger
   and writes the graph. That identity holds no INSERT on the ledger, so the
   projector cannot invent the events it then projects.

CLAUDE.md 5.3 makes the projector the only writer of the Evidence Graph. Running
both halves in one session with one role would leave that rule enforced by
nothing but this file's good intentions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import SeatDeliberator
from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.epistemo.contracts import TaskStatus
from packages.epistemo.orchestrator import CouncilOrchestrator, TaskRunReport
from packages.evidence.lifecycle import QuarantinedNode
from packages.evidence.models import EventAuditModel, ScientificEventModel
from packages.evidence.sql_ledger import SqlEventLedger
from packages.evidence.sql_projector import (
    STATUS_QUARANTINED,
    ProjectionReport,
    SqlGraphProjector,
    node_id_for,
)
from packages.kernel.database import canonical_uuid
from packages.memory.adapter import create_memory_adapter
from packages.memory.council_memory import CouncilMemory
from packages.models.contracts import ModelGateway
from packages.models.gateway import AuditedModelGateway
from packages.papers.acquisition import SourceAcquisition
from packages.papers.finding_extraction import FindingExtractor
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher
from packages.tools.gateway import AuditedToolGateway


class TaskNotRunnable(Exception):
    """Raised when a task cannot be run, with the reason kept for the caller."""


@dataclass(frozen=True, slots=True)
class JobResult:
    task_id: UUID
    run: TaskRunReport
    projection: ProjectionReport | None


async def _claim(session: AsyncSession, task_id: UUID) -> ResearchTaskModel:
    """Lock the task row, then check that it is still ours to run.

    The lock is what makes two workers safe. Without it both read QUEUED, both
    deliberate, and the ledger's idempotency keys quietly absorb the duplicate --
    which looks like it worked while burning the budget twice. The second worker
    here blocks until the first commits and then sees a status that is no longer
    QUEUED.
    """
    row = await session.scalar(
        select(ResearchTaskModel)
        .where(ResearchTaskModel.task_id == task_id)
        .with_for_update()
    )
    if row is None:
        raise TaskNotRunnable(f"task {task_id} does not exist")
    if row.status != TaskStatus.QUEUED:
        raise TaskNotRunnable(
            f"task {task_id} is {row.status}, not {TaskStatus.QUEUED}"
        )
    return row


def _budget_for(row: ResearchTaskModel) -> BudgetTracker:
    return BudgetTracker(
        limits=ResearchBudget(
            wall_clock_minutes=row.wall_clock_minutes,
            model_cost_usd=Decimal(row.model_cost_usd),
            tool_call_limit=row.tool_call_limit,
            source_limit=row.source_limit,
        )
    )


async def _confirmed_claim_ids(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[UUID, ...]:
    result = await session.execute(
        select(AtomicClaimModel.id)
        .where(
            AtomicClaimModel.task_id == task_id,
            AtomicClaimModel.status == CLAIM_CONFIRMED,
        )
        .order_by(AtomicClaimModel.created_at, AtomicClaimModel.id)
    )
    # canonical_uuid at the driver boundary: asyncpg returns its own UUID
    # subclass, and the frozen contracts these ids flow into admit a leaf only
    # when its type matches exactly. See packages.kernel.database.
    return tuple(canonical_uuid(value) for value in result.scalars())


async def _quarantined_nodes(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[QuarantinedNode, ...]:
    """Load nodes an earlier run of this task quarantined, for Resurrect.

    A quarantined event never reaches ``graph_nodes`` (the projector returns
    before ``_upsert_node`` on ``AdmissionDisposition.QUARANTINE``), so the
    ledger is the only source of truth for what is quarantined and why. This
    read is legal under the ``poliscope_app`` identity that runs
    ``deliberate()``: migration 0003 grants that role ``READ`` on both
    ``scientific_events`` (already exercised by ``SqlEventLedger``) and
    ``event_audits`` -- no new grant needed.

    Real gate-driven quarantine never populates an ``attacker``/
    ``missing_evidence``/``resurrection_condition`` -- those are
    ``LifecycleService.quarantine()``'s own fields (unused in production, see
    README's known-gaps note on ``packages/evidence/lifecycle.py``). Rather
    than fabricate plausible-looking text for fields the gate never recorded,
    the honest default is used (CLAUDE.md 7): the gate's own disposition
    reasons become ``reason``, and the rest stay explicitly unrecorded.
    """
    events = await session.scalars(
        select(ScientificEventModel).where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.status == STATUS_QUARANTINED,
        )
    )
    nodes: list[QuarantinedNode] = []
    for event in events:
        audit = await session.scalar(
            select(EventAuditModel)
            .where(
                EventAuditModel.event_id == event.id,
                EventAuditModel.gate_stage == "ADMISSION",
            )
            .order_by(EventAuditModel.created_at.desc())
        )
        raw_reasons = audit.reasons.get("reasons") if audit is not None else None
        reasons = (
            tuple(str(item) for item in raw_reasons)
            if isinstance(raw_reasons, (list, tuple))
            else ()
        )
        nodes.append(
            QuarantinedNode(
                node_id=canonical_uuid(node_id_for(event)),
                reason="; ".join(reasons) if reasons else "not recorded",
                attacker="evidence_gate",
                missing_evidence="not recorded",
                resurrection_condition="not recorded",
            )
        )
    return tuple(nodes)


async def deliberate(
    session: AsyncSession,
    task_id: UUID,
    deliberator: SeatDeliberator | None = None,
    gateway: ModelGateway | None = None,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
) -> TaskRunReport:
    """Run the seven rounds and persist the resulting events and status.

    Refuses a task that is not QUEUED. Re-running a finished task would append
    the same events under the same idempotency keys and be harmless, but it would
    also reset a terminal status, and CLAUDE.md 10 wants a completed task's
    reported gaps to stay as they were.

    ``deliberator`` overrides ``gateway``; passing neither runs the protocol with
    every seat reported unavailable, which is what a deployment with no model
    provider should honestly produce.
    """
    task = await _claim(session, task_id)
    budget = _budget_for(task)
    if deliberator is None and gateway is not None:
        # Every model call goes through the gateway, audited, per CLAUDE.md 8.
        # With no gateway the run still happens and reports every seat as
        # unavailable, which is the truthful outcome rather than a silent
        # success.
        deliberator = GatewayDeliberator(
            AuditedModelGateway(gateway, session), budget
        )
    orchestrator = CouncilOrchestrator(
        ledger=SqlEventLedger(session),
        budget=budget,
        deliberator=deliberator,
        # Process memory, per CLAUDE.md 6. It is created per run rather than per
        # process so one task's recall can never leak into another's.
        memory=CouncilMemory(create_memory_adapter(), task_id),
        # Only when a tool provider is configured. Without one the acquisition
        # round records requests and reports the gap, per CLAUDE.md 7 and 10.
        acquirer=(
            None
            if tools is None
            else SourceAcquisition(
                session, AuditedToolGateway(tools, session), task_id, budget
            )
        ),
        # Needs both a tool provider (open access lookup) and a model provider
        # (extraction call); missing either leaves every acquired source at
        # Level B and the gap recorded, same honesty rule as acquirer above.
        finding_extractor=(
            None
            if tools is None or gateway is None
            else FindingExtractor(
                session,
                AuditedToolGateway(tools, session),
                AuditedModelGateway(gateway, session),
                task_id,
                budget,
                fulltext_fetcher=fulltext_fetcher,
            )
        ),
    )
    report = await orchestrator.run(
        task_id=task_id,
        question=task.question,
        confirmed_claims=await _confirmed_claim_ids(session, task_id),
        quarantined=await _quarantined_nodes(session, task_id),
    )
    await ResearchRepository(session).set_status(task_id, report.final_status)
    return report


async def project(session: AsyncSession, task_id: UUID) -> ProjectionReport:
    """Admit the committed events into the graph under the projector identity."""
    return await SqlGraphProjector(session).project_pending(task_id)


async def run_task(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    deliberator: SeatDeliberator | None = None,
    gateway: ModelGateway | None = None,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
) -> JobResult:
    """Deliberate, commit, then project.

    Projection failing does not undo the deliberation. The events are already
    durable and the checkpoint has not moved, so the next pass reprocesses
    exactly the events that were not admitted -- which is the resume behaviour
    CLAUDE.md 10 asks for, rather than a lost round.
    """
    async with app_sessions() as session:
        try:
            report = await deliberate(
                session, task_id, deliberator, gateway, tools, fulltext_fetcher
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    async with projector_sessions() as session:
        try:
            projection = await project(session, task_id)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    return JobResult(task_id=task_id, run=report, projection=projection)


__all__ = [
    "JobResult",
    "TaskNotRunnable",
    "deliberate",
    "project",
    "run_task",
]
