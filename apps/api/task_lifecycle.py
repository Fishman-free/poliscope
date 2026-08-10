"""Task-deletion cascade shared by session deletion and account deletion.

Deleting a task physically removes every record that belongs to it -- claims,
ledger events, process stream, graph, audit rows, council rounds, papers
findings -- and the task row itself. The records span every module's tables,
so this lives in the API layer rather than any one module's repository: no
single module owns the task's lifecycle (CLAUDE.md 9). Order matters only
where one child FK-references another; those go first via a join, then every
task-scoped child, then the task row itself.

``delete_task_cascade`` is used by ``apps/api/routers/tasks.py`` for
session-history deletion and by ``apps/api/routers/account.py`` when an
account is deleted (its tasks go with it).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.council.models import (
    CouncilRoundModel,
    RoundOutputModel,
    ScientistRunModel,
)
from packages.evidence.models import (
    EventAuditModel,
    GraphEdgeModel,
    GraphNodeModel,
    ProcessStreamModel,
    ProjectionCheckpointModel,
    ScientificEventModel,
)
from packages.models.models import ModelCallModel
from packages.papers.models import (
    CitationAnchorModel,
    FindingModel,
    ObjectModel,
    SourceModel,
    SourceVersionModel,
    StudyModel,
)
from packages.research.models import (
    AtomicClaimModel,
    ResearchScopeModel,
    ResearchTaskModel,
)
from packages.tools.models import ToolCallModel

_TASK_SCOPED_CHILDREN: tuple[type[object], ...] = (
    ScientificEventModel,
    ProcessStreamModel,
    ModelCallModel,
    ToolCallModel,
    ObjectModel,
    SourceModel,
    CouncilRoundModel,
    GraphEdgeModel,
    GraphNodeModel,
    ProjectionCheckpointModel,
    AtomicClaimModel,
    ResearchScopeModel,
)


async def delete_task_cascade(session: AsyncSession, task_id: UUID) -> None:
    """Permanently delete one task and every record that belongs to it.

    Caller owns the transaction (commits on success, rolls back on failure).
    """
    # 1) Deep parent-first, because these children FK-reference other children
    #    of the same task (order matters):
    #    citation_anchors -> findings -> studies -> source_versions -> sources
    #    event_audits -> scientific_events
    #    scientist_runs / round_outputs -> council_rounds
    await session.execute(
        delete(EventAuditModel).where(
            EventAuditModel.event_id.in_(
                select(ScientificEventModel.id).where(
                    ScientificEventModel.task_id == task_id
                )
            )
        )
    )
    await session.execute(
        delete(CitationAnchorModel).where(
            CitationAnchorModel.finding_id.in_(
                select(FindingModel.id)
                .join(StudyModel, StudyModel.id == FindingModel.study_id)
                .join(
                    SourceVersionModel,
                    SourceVersionModel.id == StudyModel.source_version_id,
                )
                .join(SourceModel, SourceModel.id == SourceVersionModel.source_id)
                .where(SourceModel.task_id == task_id)
            )
        )
    )
    await session.execute(
        delete(FindingModel).where(
            FindingModel.study_id.in_(
                select(StudyModel.id)
                .join(
                    SourceVersionModel,
                    SourceVersionModel.id == StudyModel.source_version_id,
                )
                .join(SourceModel, SourceModel.id == SourceVersionModel.source_id)
                .where(SourceModel.task_id == task_id)
            )
        )
    )
    await session.execute(
        delete(StudyModel).where(
            StudyModel.source_version_id.in_(
                select(SourceVersionModel.id)
                .join(SourceModel, SourceModel.id == SourceVersionModel.source_id)
                .where(SourceModel.task_id == task_id)
            )
        )
    )
    await session.execute(
        delete(ScientistRunModel).where(
            ScientistRunModel.round_id.in_(
                select(CouncilRoundModel.id).where(
                    CouncilRoundModel.task_id == task_id
                )
            )
        )
    )
    await session.execute(
        delete(RoundOutputModel).where(
            RoundOutputModel.round_id.in_(
                select(CouncilRoundModel.id).where(
                    CouncilRoundModel.task_id == task_id
                )
            )
        )
    )
    await session.execute(
        delete(SourceVersionModel).where(
            SourceVersionModel.source_id.in_(
                select(SourceModel.id).where(SourceModel.task_id == task_id)
            )
        )
    )

    # 2) Every task-scoped child that has no intra-task FK dependency.
    for model in _TASK_SCOPED_CHILDREN:
        await session.execute(delete(model).where(model.task_id == task_id))  # type: ignore[attr-defined]

    # 3) The task row itself.
    await session.execute(
        delete(ResearchTaskModel).where(ResearchTaskModel.task_id == task_id)
    )
