"""The workspace snapshot that every view renders from.

One endpoint serves the whole workspace rather than one endpoint per panel, so
that the Research Brief, the Controversy Map, and the council status can never
show state from three different moments. ``workspace_version`` is the ledger
sequence the snapshot was taken at, which is what lets the client tell whether
an arriving SSE event is already reflected in what it is showing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import SessionDep
from apps.api.schemas import SafetyNotice, WorkspaceSnapshot
from packages.evidence.independence import LineageLink, cluster_evidence
from packages.evidence.models import GraphEdgeModel, GraphNodeModel
from packages.evidence.sql_ledger import SqlEventLedger
from packages.kernel.contracts import FrozenDict
from packages.kernel.database import canonical_uuid
from packages.papers.models import SourceModel
from packages.reports.json_export import to_dict
from packages.reports.service import ReportService
from packages.research.repository import ResearchRepository, TaskNotFound

router = APIRouter()

# Node types that carry their own panel in the workspace. Anything else belongs
# to the graph and is returned inside it.
BLINDSPOT_NODE_TYPE = "Blindspot"
DISCRIMINATING_STUDY_NODE_TYPE = "DiscriminatingStudy"
DEBATE_CAPSULE_NODE_TYPE = "DebateCapsule"


async def _nodes_of_type(
    session: AsyncSession,
    task_id: UUID,
    node_type: str,
) -> tuple[FrozenDict[str, object], ...]:
    result = await session.execute(
        select(GraphNodeModel)
        .where(
            GraphNodeModel.task_id == task_id,
            GraphNodeModel.node_type == node_type,
        )
        .order_by(GraphNodeModel.created_at)
    )
    return tuple(
        FrozenDict({"id": str(row.id), "status": row.status, **dict(row.payload)})
        for row in result.scalars()
    )


async def _graph(session: AsyncSession, task_id: UUID) -> FrozenDict[str, object]:
    """Return every node and edge, including refuted and quarantined ones.

    Filtering out refuted nodes here would make the map look cleaner and would
    silently violate CLAUDE.md 4: a rebutted position stays traceable. The client
    decides what to dim, not the server.
    """
    nodes = await session.execute(
        select(GraphNodeModel)
        .where(GraphNodeModel.task_id == task_id)
        .order_by(GraphNodeModel.created_at)
    )
    edges = await session.execute(
        select(GraphEdgeModel)
        .where(GraphEdgeModel.task_id == task_id)
        .order_by(GraphEdgeModel.created_at)
    )
    return FrozenDict(
        {
            "nodes": tuple(
                {
                    "id": str(row.id),
                    "node_type": row.node_type,
                    "status": row.status,
                    "payload": dict(row.payload),
                }
                for row in nodes.scalars()
            ),
            "edges": tuple(
                {
                    "id": str(row.id),
                    "source": str(row.source_node_id),
                    "target": str(row.target_node_id),
                    "edge_type": row.edge_type,
                }
                for row in edges.scalars()
            ),
        }
    )


async def _evidence_counts(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[int, int]:
    """Return paper count and independent evidence cluster count.

    CLAUDE.md 7.4 requires both numbers to reach the interface, because papers
    that share a dataset, a sample, or a research team are not independent
    evidence and a single count invites exactly that mistake.

    Only one dependency is derivable from what the database currently records: a
    shared canonical DOI, which means two source rows describe one work. Dataset
    reuse, sample overlap, and team overlap need lineage edges that are not yet
    captured, so the number below is an *upper bound* on independence. It is
    reported rather than assumed correct, and it is computed by the same
    clustering used everywhere else so that adding lineage later changes the
    input and not the rule.
    """
    result = await session.execute(
        select(SourceModel.id, SourceModel.canonical_doi).where(
            SourceModel.task_id == task_id
        )
    )
    rows = list(result)
    sources = [canonical_uuid(row.id) for row in rows]
    # A source with no canonical DOI stays its own cluster: unknown identity must
    # not silently merge two papers into one piece of evidence.
    dependencies: tuple[LineageLink, ...] = tuple(
        (canonical_uuid(row.id), "PREPRINT_VERSION_OF", row.canonical_doi)
        for row in rows
        if row.canonical_doi
    )
    clusters = cluster_evidence(sources, dependencies)
    return clusters.paper_count, clusters.independent_cluster_count


@router.get("/{task_id}", response_model=WorkspaceSnapshot)
async def get_workspace(task_id: UUID, session: SessionDep) -> WorkspaceSnapshot:
    try:
        task = await ResearchRepository(session).get_task(task_id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        ) from error

    # The brief is built from the same session and the same moment as the graph
    # below, so the Research Brief panel cannot show a different conclusion from
    # the Controversy Map beside it.
    brief = to_dict(await ReportService(session).build(task_id))
    paper_count, cluster_count = await _evidence_counts(session, task_id)
    version = await SqlEventLedger(session).latest_sequence(task_id)
    task_payload: dict[str, Any] = {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
    }
    return WorkspaceSnapshot(
        task=FrozenDict(task_payload),
        brief=FrozenDict(brief),
        seats=(),
        graph=await _graph(session, task_id),
        blindspots=await _nodes_of_type(session, task_id, BLINDSPOT_NODE_TYPE),
        discriminating_studies=await _nodes_of_type(
            session, task_id, DISCRIMINATING_STUDY_NODE_TYPE
        ),
        dissents=await _nodes_of_type(session, task_id, DEBATE_CAPSULE_NODE_TYPE),
        evolution=(),
        paper_count=paper_count,
        independent_cluster_count=cluster_count,
        workspace_version=version,
        safety_notice=SafetyNotice(),
    )
