"""The workspace snapshot that every view renders from.

One endpoint serves the whole workspace rather than one endpoint per panel, so
that the Research Brief, the Controversy Map, and the council status can never
show state from three different moments. ``workspace_version`` is the ledger
sequence the snapshot was taken at, which is what lets the client tell whether
an arriving SSE event is already reflected in what it is showing.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUserDep, SessionDep
from apps.api.schemas import SafetyNotice, WorkspaceSnapshot
from packages.council.rounds.registry import (
    CHALLENGE_RAISED,
    CONFIDENCE_UPDATED,
    FINAL_JUDGMENT,
    PRECOMMITMENT_SEALED,
    SEAT_UNAVAILABLE,
)
from packages.epistemo.orchestrator import ORDERED_SEATS
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.lineage_view import LineageViewRow, build_lineage_view
from packages.evidence.models import (
    EventAuditModel,
    GraphEdgeModel,
    GraphNodeModel,
    ScientificEventModel,
)
from packages.evidence.sql_ledger import SqlEventLedger
from packages.evidence.sql_projector import STATUS_QUARANTINED, node_id_for
from packages.kernel.contracts import FrozenDict
from packages.kernel.database import canonical_uuid
from packages.models.usage import TaskBudget, aggregate_task_usage
from packages.papers.models import SourceModel
from packages.reports.json_export import to_dict
from packages.reports.safety import sanitize_export
from packages.reports.service import ReportService
from packages.reports.synthesis import FINAL_PAPER_DRAFTED
from packages.research.repository import (
    ResearchRepository,
    StoredTask,
    TaskNotFound,
)

router = APIRouter()

# Node types that carry their own panel in the workspace. Anything else belongs
# to the graph and is returned inside it.
BLINDSPOT_NODE_TYPE = "Blindspot"
DISCRIMINATING_STUDY_NODE_TYPE = "DiscriminatingStudy"
DISSENT_CERTIFICATE_NODE_TYPE = "DissentCertificate"

# Events the Evolution View draws from: only ones that name a claim, either
# through the ledger's own ``claim_id`` column (``_fork_events`` and, since
# plan phase 5, every ``CONFIDENCE_UPDATED`` marker) or a claim reference
# inside their payload (a challenge's ``claim_id``, a dissent certificate's
# ``target_id``). Deliberately narrower than the Audit Trail, which is design
# spec 8's own separate panel and covers every process event, not just
# claim-referencing ones.
_EVOLUTION_EVENT_TYPES = (
    EvidenceNodeType.CLAIM.value,
    CHALLENGE_RAISED,
    EvidenceNodeType.DISSENT_CERTIFICATE.value,
    CONFIDENCE_UPDATED,
)


async def _latest_payloads(
    session: AsyncSession,
    task_id: UUID,
    event_types: tuple[str, ...],
) -> dict[str, FrozenDict[str, object] | None]:
    """The most recent ledger event of each requested type, in ONE query.

    History-session latency fix: the conditioned consensus (CONSENSUS_DRAFTED)
    and the final paper (FINAL_PAPER_DRAFTED) used to cost one round trip each,
    and the consensus was fetched twice (snapshot + adjudication). One ordered
    scan keeps the first row per type -- that row is its latest payload.
    """
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type.in_(event_types),
        )
        .order_by(
            ScientificEventModel.event_type,
            ScientificEventModel.sequence.desc(),
        )
    )
    latest: dict[str, FrozenDict[str, object]] = {}
    for row in result.scalars():
        if row.event_type not in latest:
            latest[row.event_type] = FrozenDict(dict(row.payload))
    return {event_type: latest.get(event_type) for event_type in event_types}


# The three node families that get their own workspace panel. They are
# fetched together so opening a history session does not pay three sequential
# graph-node scans (history-session latency fix).
_PANEL_NODE_TYPES = (
    BLINDSPOT_NODE_TYPE,
    DISCRIMINATING_STUDY_NODE_TYPE,
    DISSENT_CERTIFICATE_NODE_TYPE,
)


async def _panel_nodes(
    session: AsyncSession,
    task_id: UUID,
) -> dict[str, tuple[FrozenDict[str, object], ...]]:
    result = await session.execute(
        select(GraphNodeModel)
        .where(
            GraphNodeModel.task_id == task_id,
            GraphNodeModel.node_type.in_(_PANEL_NODE_TYPES),
        )
        .order_by(GraphNodeModel.created_at)
    )
    grouped: dict[str, list[FrozenDict[str, object]]] = {
        node_type: [] for node_type in _PANEL_NODE_TYPES
    }
    for row in result.scalars():
        grouped[row.node_type].append(
            FrozenDict(
                {"id": str(row.id), "status": row.status, **dict(row.payload)}
            )
        )
    return {
        node_type: tuple(rows) for node_type, rows in grouped.items()
    }


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


# B6: process-only event recording a researcher's manual adjudication of a
# merge candidate or quarantined node. It is never projected into the Evidence
# Graph (the projector allowlist ignores it): a researcher directs attention
# but cannot bypass the evidence gate (AGENTS.md principle 8), so this is an
# auditable decision record, not a graph mutation.
RESEARCHER_ADJUDICATION = "RESEARCHER_ADJUDICATION"
CONSENSUS_DRAFTED = "CONSENSUS_DRAFTED"


async def _lineage(
    session: AsyncSession,
    task_id: UUID,
) -> FrozenDict[str, object]:
    """A1: full evidence-lineage view -- sources, dependency links, clusters.

    Uses the same detect_lineage + cluster_evidence rule as the paper/cluster
    counts (CLAUDE.md 7.4), but returns the structure behind the numbers so the
    Evidence Lineage view can show why N papers collapse into M independent
    clusters: shared dataset/sample/preprint links merge, shared authorship is
    shown but never merges.
    """
    result = await session.execute(
        select(
            SourceModel.id,
            SourceModel.doi,
            SourceModel.canonical_doi,
            SourceModel.dataset_id,
            SourceModel.authors,
            SourceModel.title,
            SourceModel.publication_year,
        ).where(SourceModel.task_id == task_id)
    )
    rows = list(result)
    view = build_lineage_view(
        [
            LineageViewRow(
                source_id=canonical_uuid(row.id),
                title=row.title or "",
                doi=row.doi,
                canonical_doi=row.canonical_doi,
                dataset_id=row.dataset_id,
                authors=tuple(row.authors or ()),
                publication_year=row.publication_year,
            )
            for row in rows
        ]
    )
    return FrozenDict(view)


async def _adjudication_decided_keys(
    session: AsyncSession, task_id: UUID
) -> set[str]:
    """Candidate/node keys the researcher has already adjudicated."""
    result = await session.execute(
        select(ScientificEventModel).where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type == RESEARCHER_ADJUDICATION,
        )
    )
    decided: set[str] = set()
    for row in result.scalars():
        target = row.payload.get("target_key")
        if isinstance(target, str):
            decided.add(target)
    return decided


async def _adjudication(
    session: AsyncSession,
    task_id: UUID,
    *,
    consensus: FrozenDict[str, object] | None = None,
) -> FrozenDict[str, object]:
    """B6: pending merge candidates + quarantined nodes, with their reasons.

    Merge candidates are the CONSENSUS_DRAFTED ``merge_candidates`` (unresolved
    conflicts the joint round deliberately does *not* auto-merge). Quarantined
    nodes come from STATUS_QUARANTINED ledger events joined to their ADMISSION
    audit reasons (the same read the worker's Resurrect path uses). Anything
    already carrying a RESEARCHER_ADJUDICATION is marked resolved.
    """
    decided = await _adjudication_decided_keys(session, task_id)
    if consensus is None:
        consensus = (
            await _latest_payloads(
                session, task_id, (CONSENSUS_DRAFTED,)
            )
        )[CONSENSUS_DRAFTED]
    raw_candidates = (
        consensus.get("merge_candidates", ()) if consensus is not None else ()
    )
    candidate_items = (
        raw_candidates
        if isinstance(raw_candidates, (list, tuple))
        else ()
    )
    merge_candidates = tuple(
        FrozenDict(
            {
                "key": str(item),
                "description": str(item),
                "resolved": str(item) in decided,
            }
        )
        for item in candidate_items
    )

    quarantined_event_rows = list(
        await session.scalars(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.status == STATUS_QUARANTINED,
            )
        )
    )
    # N+1 fix: one batched ADMISSION-audit lookup for every quarantined
    # event instead of one round trip per event (history-session latency).
    admission_audits: dict[UUID, EventAuditModel] = {}
    if quarantined_event_rows:
        audit_rows = await session.scalars(
            select(EventAuditModel)
            .where(
                EventAuditModel.event_id.in_(
                    [event.id for event in quarantined_event_rows]
                ),
                EventAuditModel.gate_stage == "ADMISSION",
            )
            .order_by(EventAuditModel.created_at)
        )
        for audit_row in audit_rows:
            # ascending order + overwrite keeps the newest audit per event
            admission_audits[audit_row.event_id] = audit_row
    quarantined: list[FrozenDict[str, object]] = []
    for event in quarantined_event_rows:
        audit = admission_audits.get(event.id)
        raw_reasons = audit.reasons.get("reasons") if audit is not None else None
        reasons = (
            tuple(str(item) for item in raw_reasons)
            if isinstance(raw_reasons, (list, tuple))
            else ()
        )
        node_key = str(node_id_for(event))
        quarantined.append(
            FrozenDict(
                {
                    "node_id": node_key,
                    "event_type": event.event_type,
                    "sequence": event.sequence,
                    "reasons": reasons,
                    "resolved": node_key in decided,
                }
            )
        )
    return FrozenDict(
        {
            "merge_candidates": merge_candidates,
            "quarantined": tuple(quarantined),
        }
    )


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
    # History-session latency fix: the council panel reads only four
    # seat event families; list_since() scanned the WHOLE ledger and hauled
    # paper drafts / consensus blobs into memory on every workspace open.
    seat_result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type.in_(
                (
                    PRECOMMITMENT_SEALED,
                    CHALLENGE_RAISED,
                    FINAL_JUDGMENT,
                    SEAT_UNAVAILABLE,
                )
            ),
        )
        .order_by(ScientificEventModel.sequence)
    )
    events = list(seat_result.scalars())
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
    """Chronological feed of Claim-referencing events: forks, challenges,
    dissents, and (since plan phase 5) qualitative confidence markers.

    A task with no fork, challenge, dissent, or confidence marker against a
    claim produces an empty feed -- the honest result of what the council
    actually recorded, not a bug. ``CONFIDENCE_UPDATED``
    (``packages.council.rounds.registry._confidence_marker``) gives every
    confirmed claim a point at each of EVIDENCE_EXCHANGE, CROSS_EXAMINATION,
    JOINT_MODELING, and FINAL_REJUDGMENT, so the client can now draw a
    continuous per-claim trajectory across those four phases rather than only
    the sparse discrete events a claim happened to be challenged or forked in.
    That marker's ``confidence_delta_note`` is a plain-language sentence, not
    a number -- CLAUDE.md 16 forbids treating a model's confidence as a
    substitute for real statistical uncertainty, and no model in this MVP
    computes an actual confidence delta for a claim, so this stays a
    qualitative trajectory, not a fabricated quantitative curve.
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


async def _assemble_snapshot(
    session: AsyncSession,
    task: StoredTask,
    *,
    public: bool = False,
) -> WorkspaceSnapshot:
    """Build the one-moment workspace snapshot for one task.

    Shared by the authenticated workspace endpoint and the A2 public share
    endpoint so a shared link shows exactly what the owner sees, minus the
    account-private cost usage and share metadata, and with a signed-URL/local
    -path redaction pass (``public=True``).
    """
    task_id = task.task_id
    # The brief is built from the same session and the same moment as the graph
    # below, so the Research Brief panel cannot show a different conclusion from
    # the Controversy Map beside it.
    brief = to_dict(await ReportService(session).build(task_id))
    lineage = await _lineage(session, task_id)
    paper_count = int(cast(int, lineage["paper_count"]))
    cluster_count = int(
        cast(int, lineage["independent_cluster_count"])
    )
    version = await SqlEventLedger(session).latest_sequence(task_id)
    usage = None if public else await aggregate_task_usage(
        session,
        task_id,
        TaskBudget(
            model_cost_usd=Decimal(task.model_cost_usd),
            tool_call_limit=task.tool_call_limit,
            source_limit=task.source_limit,
        ),
    )
    corpus_cutoff = task.corpus_cutoff
    replay_of_task_id = task.replay_of_task_id
    share_created_at = task.share_created_at
    task_payload: dict[str, Any] = {
        "task_id": str(task.task_id),
        "question": task.question,
        "status": task.status,
        "created_by": task.created_by,
        "task_type": task.task_type,
        "corpus_cutoff": (
            corpus_cutoff.isoformat() if corpus_cutoff is not None else None
        ),
        "replay_of_task_id": (
            str(replay_of_task_id) if replay_of_task_id is not None else None
        ),
    }
    if not public:
        task_payload["has_share"] = task.share_token is not None
        task_payload["share_created_at"] = (
            share_created_at.isoformat()
            if share_created_at is not None
            else None
        )
    # History-session latency fix: the three panel node families load
    # in one query, and the two single-document payloads load together; the
    # consensus fetched here is reused by _adjudication instead of re-read.
    panel_nodes = await _panel_nodes(session, task_id)
    latest_payloads = await _latest_payloads(
        session,
        task_id,
        (FINAL_PAPER_DRAFTED, CONSENSUS_DRAFTED),
    )
    snapshot = WorkspaceSnapshot(
        task=FrozenDict(task_payload),
        brief=FrozenDict(brief),
        seats=await _seats(session, task_id),
        graph=await _graph(session, task_id),
        blindspots=panel_nodes[BLINDSPOT_NODE_TYPE],
        discriminating_studies=panel_nodes[DISCRIMINATING_STUDY_NODE_TYPE],
        dissents=panel_nodes[DISSENT_CERTIFICATE_NODE_TYPE],
        evolution=await _evolution(session, task_id),
        paper_count=paper_count,
        independent_cluster_count=cluster_count,
        lineage=lineage,
        adjudication=await _adjudication(
            session,
            task_id,
            consensus=latest_payloads[CONSENSUS_DRAFTED],
        ),
        usage=None if usage is None else FrozenDict(usage),
        workspace_version=version,
        safety_notice=SafetyNotice(),
        paper=latest_payloads[FINAL_PAPER_DRAFTED],
        consensus=latest_payloads[CONSENSUS_DRAFTED],
    )
    if not public:
        return snapshot
    return redact_public_snapshot(snapshot)


def redact_public_snapshot(snapshot: WorkspaceSnapshot) -> WorkspaceSnapshot:
    """A2: strip private fields and redact signed URLs/local paths for sharing."""
    import json

    data = snapshot.model_dump(mode="json")
    # Cost usage is account-private; a shared reader never sees it.
    data["usage"] = None
    raw = sanitize_export(json.dumps(data, ensure_ascii=False))
    return WorkspaceSnapshot.model_validate(json.loads(raw))


@router.get("/{task_id}", response_model=WorkspaceSnapshot)
async def get_workspace(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> WorkspaceSnapshot:
    try:
        task = await ResearchRepository(session).get_task(task_id, current_user.id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        ) from error
    return await _assemble_snapshot(session, task)
