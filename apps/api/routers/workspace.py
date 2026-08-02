"""The workspace snapshot that every view renders from.

One endpoint serves the whole workspace rather than one endpoint per panel, so
that the Research Brief, the Controversy Map, and the council status can never
show state from three different moments. ``workspace_version`` is the ledger
sequence the snapshot was taken at, which is what lets the client tell whether
an arriving SSE event is already reflected in what it is showing.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import SessionDep
from apps.api.schemas import SafetyNotice, WorkspaceSnapshot
from packages.council.rounds.registry import (
    CHALLENGE_RAISED,
    FINAL_JUDGMENT,
    PRECOMMITMENT_SEALED,
    SEAT_UNAVAILABLE,
)
from packages.epistemo.orchestrator import ORDERED_SEATS
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.independence import cluster_evidence
from packages.evidence.lineage_detection import LineageSourceRow, detect_lineage
from packages.evidence.models import (
    GraphEdgeModel,
    GraphNodeModel,
    ScientificEventModel,
)
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
DISSENT_CERTIFICATE_NODE_TYPE = "DissentCertificate"

# Events the Evolution View draws from: only ones that name a claim, either
# through the ledger's own ``claim_id`` column (today only ``_fork_events``
# sets it) or a claim reference inside their payload (a challenge's
# ``claim_id``, a dissent certificate's ``target_id``). Deliberately narrower
# than the Audit Trail, which is design spec 8's own separate panel and covers
# every process event, not just claim-referencing ones.
_EVOLUTION_EVENT_TYPES = (
    EvidenceNodeType.CLAIM.value,
    CHALLENGE_RAISED,
    EvidenceNodeType.DISSENT_CERTIFICATE.value,
)


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

    Shared canonical DOI (two rows describing one work) and shared
    ``dataset_id`` both merge clusters. Shared authorship never merges --
    CLAUDE.md 4 treats two datasets from one lab as two datasets -- but is
    still detected so a future view can surface it. No current provider
    adapter resolves a dataset identifier, so in practice ``dataset_id`` is
    ``None`` for essentially every row today; this is an upper bound on
    independence, not a guarantee, and is computed by the same clustering used
    everywhere else so that wiring a real dataset-id source later changes the
    input and not the rule.
    """
    result = await session.execute(
        select(
            SourceModel.id,
            SourceModel.canonical_doi,
            SourceModel.dataset_id,
            SourceModel.authors,
        ).where(SourceModel.task_id == task_id)
    )
    rows = list(result)
    sources = [canonical_uuid(row.id) for row in rows]
    # A source with no canonical DOI stays its own cluster: unknown identity must
    # not silently merge two papers into one piece of evidence.
    dependencies = detect_lineage(
        [
            LineageSourceRow(
                source_id=canonical_uuid(row.id),
                canonical_doi=row.canonical_doi,
                dataset_id=row.dataset_id,
                authors=tuple(row.authors),
            )
            for row in rows
        ]
    )
    clusters = cluster_evidence(sources, dependencies)
    return clusters.paper_count, clusters.independent_cluster_count


async def _seats(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[FrozenDict[str, object], ...]:
    """Per-seat structured summary: precommitment, challenges, final judgment.

    Built only from process events that already carry a ``"seat"`` payload
    key -- ``PRECOMMITMENT_SEALED``, ``CHALLENGE_RAISED``, ``FINAL_JUDGMENT``,
    ``SEAT_UNAVAILABLE`` (all defined in
    ``packages.council.rounds.registry``). CLAUDE.md 11 requires the council
    panel to show only structured actions, evidence used, challenges and
    responses, and confidence changes -- never a seat's private chain of
    thought, which is why nothing here reads a model's raw response text
    beyond the same self-reported strings the round itself already emitted.
    """
    events = await SqlEventLedger(session).list_since(task_id)
    precommitments: dict[str, dict[str, object]] = {}
    challenges: dict[str, list[dict[str, object]]] = defaultdict(list)
    final_judgments: dict[str, dict[str, object]] = {}
    unavailable_phases: dict[str, list[str]] = defaultdict(list)
    for entry in events:
        seat_value = entry.payload.get("seat")
        if not isinstance(seat_value, str):
            continue
        if entry.event_type == PRECOMMITMENT_SEALED:
            precommitments[seat_value] = {
                "confidence": entry.payload.get("confidence"),
                "update_condition": entry.payload.get("update_condition"),
            }
        elif entry.event_type == CHALLENGE_RAISED:
            challenges[seat_value].append(
                {
                    "claim_id": entry.payload.get("claim_id"),
                    "statement": entry.payload.get("statement"),
                    "is_fatal": entry.payload.get("is_fatal"),
                }
            )
        elif entry.event_type == FINAL_JUDGMENT:
            final_judgments[seat_value] = {
                "final_judgment": entry.payload.get("final_judgment"),
                "confidence": entry.payload.get("confidence"),
                "has_dissent": entry.payload.get("has_dissent"),
            }
        elif entry.event_type == SEAT_UNAVAILABLE:
            phase = entry.payload.get("phase")
            if isinstance(phase, str):
                unavailable_phases[seat_value].append(phase)

    return tuple(
        FrozenDict(
            {
                "seat": seat.value,
                "precommitment": precommitments.get(seat.value),
                "challenges_raised": tuple(challenges.get(seat.value, ())),
                "final_judgment": final_judgments.get(seat.value),
                "unavailable_phases": tuple(unavailable_phases.get(seat.value, ())),
            }
        )
        for seat in ORDERED_SEATS
    )


async def _evolution(
    session: AsyncSession,
    task_id: UUID,
) -> tuple[FrozenDict[str, object], ...]:
    """Chronological feed of Claim-referencing events: forks, challenges, dissents.

    A task with no fork, challenge, or dissent against a claim produces an
    empty feed -- the honest result of what the council actually recorded,
    not a bug. No round in ``packages/council/rounds/registry.py`` currently
    emits a dedicated "confidence changed" event for a Claim, so this cannot
    show a continuous confidence curve, only the discrete events that do
    exist; see README's known-gaps entry for the same limitation.
    """
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type.in_(_EVOLUTION_EVENT_TYPES),
        )
        .order_by(ScientificEventModel.sequence)
    )
    entries: list[FrozenDict[str, object]] = []
    for row in result.scalars():
        payload = dict(row.payload)
        if row.claim_id is not None:
            claim_id: str | None = str(row.claim_id)
        elif row.event_type == CHALLENGE_RAISED:
            raw_claim_id = payload.get("claim_id")
            claim_id = str(raw_claim_id) if raw_claim_id else None
        elif row.event_type == EvidenceNodeType.DISSENT_CERTIFICATE.value:
            raw_target_id = payload.get("target_id")
            claim_id = str(raw_target_id) if raw_target_id else None
        else:
            claim_id = None
        entries.append(
            FrozenDict(
                {
                    "sequence": row.sequence,
                    "event_type": row.event_type,
                    "status": row.status,
                    "claim_id": claim_id,
                    "payload": payload,
                }
            )
        )
    return tuple(entries)


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
        seats=await _seats(session, task_id),
        graph=await _graph(session, task_id),
        blindspots=await _nodes_of_type(session, task_id, BLINDSPOT_NODE_TYPE),
        discriminating_studies=await _nodes_of_type(
            session, task_id, DISCRIMINATING_STUDY_NODE_TYPE
        ),
        dissents=await _nodes_of_type(
            session, task_id, DISSENT_CERTIFICATE_NODE_TYPE
        ),
        evolution=await _evolution(session, task_id),
        paper_count=paper_count,
        independent_cluster_count=cluster_count,
        workspace_version=version,
        safety_notice=SafetyNotice(),
    )
