"""Assembles the Research Brief from the graph, the ledger, and the task row.

The renderers in this package took five lists and printed their lengths; nothing
supplied the lists and nothing called the renderers. This module reads the real
state and produces the brief CLAUDE.md 11 asks for.

Three rules shape what comes out:

* **Conclusions and limitations are the same section.** CLAUDE.md 11 requires
  them side by side. A brief that lists findings first and caveats last is read
  as findings.
* **Nothing is dropped for being inconvenient.** Refuted nodes, quarantined
  events, dissents, and unfilled evidence slots all appear. CLAUDE.md 4 forbids a
  silently deleted minority position and CLAUDE.md 10 forbids a complete-looking
  report over incomplete evidence.
* **Papers are counted separately from independent evidence.** CLAUDE.md 7.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.independence import LineageLink, cluster_evidence
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.kernel.database import canonical_uuid
from packages.papers.models import SourceModel
from packages.research.repository import (
    CLAIM_CONFIRMED,
    ResearchRepository,
    StoredClaim,
)

# Ledger statuses that mean the event never became evidence. Each is a limit on
# what the brief may claim, so each is reported rather than filtered away.
NOT_ADMITTED = ("quarantined", "discovery_only", "tool_lead_only")

# Process events the brief reads for its limitations section.
SEAT_UNAVAILABLE = "SEAT_UNAVAILABLE"
PHASE_FAILED = "PHASE_FAILED"
PHASE_SKIPPED = "PHASE_SKIPPED"

# A brief about mental health carries the notice in CLAUDE.md 16. Matched on the
# question rather than configured per deployment, because the first domain is
# digital behaviour and mental health and forgetting the flag is the likely
# failure.
_MENTAL_HEALTH_TERMS = (
    "depress",
    "anxiet",
    "mental health",
    "suicid",
    "self-harm",
    "wellbeing",
    "well-being",
    "心理",
    "抑郁",
    "焦虑",
)


@dataclass(frozen=True, slots=True)
class BriefNode:
    node_id: UUID
    node_type: str
    status: str
    payload: dict[str, object]


@dataclass
class ResearchBrief:
    """Everything a brief renders from, with its limits attached."""

    task_id: UUID
    question: str
    status: str
    confirmed_claims: tuple[StoredClaim, ...] = ()
    findings: tuple[BriefNode, ...] = ()
    blindspots: tuple[BriefNode, ...] = ()
    dissents: tuple[BriefNode, ...] = ()
    discriminating_studies: tuple[BriefNode, ...] = ()
    refuted_or_withdrawn: tuple[BriefNode, ...] = ()
    unadmitted_events: tuple[str, ...] = ()
    absent_seats: tuple[str, ...] = ()
    failed_phases: tuple[str, ...] = ()
    skipped_phases: tuple[str, ...] = ()
    paper_count: int = 0
    independent_cluster_count: int = 0
    is_mental_health: bool = False
    limitations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_gaps(self) -> bool:
        return bool(
            self.absent_seats
            or self.failed_phases
            or self.skipped_phases
            or self.unadmitted_events
        )


def _to_node(row: GraphNodeModel) -> BriefNode:
    return BriefNode(
        node_id=canonical_uuid(row.id),
        node_type=row.node_type,
        status=row.status,
        payload=dict(row.payload),
    )


def looks_like_mental_health(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in _MENTAL_HEALTH_TERMS)


def _limitations(brief: ResearchBrief) -> tuple[str, ...]:
    """State plainly what this brief cannot support.

    Generated from the same state the conclusions are, so a limitation cannot be
    forgotten when a conclusion is added.
    """
    limits: list[str] = []
    if brief.absent_seats:
        limits.append(
            f"{len(set(brief.absent_seats))} of 7 seats could not deliberate in at "
            "least one round; their perspective is missing from the conclusions."
        )
    if brief.skipped_phases:
        limits.append(
            f"Rounds not reached: {', '.join(sorted(set(brief.skipped_phases)))}. "
            "The protocol did not complete."
        )
    if brief.failed_phases:
        limits.append(
            f"Rounds that failed: {', '.join(sorted(set(brief.failed_phases)))}."
        )
    if brief.unadmitted_events:
        limits.append(
            f"{len(brief.unadmitted_events)} submissions were refused by the "
            "evidence gate and are not part of any conclusion."
        )
    if not brief.findings:
        limits.append(
            "No study finding was admitted, so nothing here is grounded in "
            "retrieved source text."
        )
    if brief.paper_count and brief.independent_cluster_count < brief.paper_count:
        limits.append(
            f"{brief.paper_count} papers reduce to "
            f"{brief.independent_cluster_count} independent evidence clusters; "
            "paper count overstates corroboration."
        )
    if brief.refuted_or_withdrawn:
        limits.append(
            f"{len(brief.refuted_or_withdrawn)} nodes were refuted, narrowed, or "
            "withdrawn and are retained for audit rather than deleted."
        )
    limits.append(
        "Model confidence is not statistical uncertainty and does not replace "
        "expert judgment."
    )
    return tuple(limits)


class ReportService:
    """Reads one task's durable state and returns its brief."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _nodes(self, task_id: UUID) -> list[GraphNodeModel]:
        result = await self._session.execute(
            select(GraphNodeModel)
            .where(GraphNodeModel.task_id == task_id)
            .order_by(GraphNodeModel.created_at, GraphNodeModel.id)
        )
        return list(result.scalars())

    async def _events(self, task_id: UUID) -> list[ScientificEventModel]:
        result = await self._session.execute(
            select(ScientificEventModel)
            .where(ScientificEventModel.task_id == task_id)
            .order_by(ScientificEventModel.sequence)
        )
        return list(result.scalars())

    async def _clusters(self, task_id: UUID) -> tuple[int, int]:
        result = await self._session.execute(
            select(SourceModel.id, SourceModel.canonical_doi).where(
                SourceModel.task_id == task_id
            )
        )
        rows = list(result)
        dependencies: tuple[LineageLink, ...] = tuple(
            (canonical_uuid(row.id), "PREPRINT_VERSION_OF", row.canonical_doi)
            for row in rows
            if row.canonical_doi
        )
        clustered = cluster_evidence(
            [canonical_uuid(row.id) for row in rows], dependencies
        )
        return clustered.paper_count, clustered.independent_cluster_count

    async def build(self, task_id: UUID) -> ResearchBrief:
        task = await ResearchRepository(self._session).get_task(task_id)
        claims = await ResearchRepository(self._session).list_claims(task_id)
        nodes = [_to_node(row) for row in await self._nodes(task_id)]
        events = await self._events(task_id)
        papers, clusters = await self._clusters(task_id)

        def of_type(node_type: EvidenceNodeType) -> tuple[BriefNode, ...]:
            return tuple(
                node for node in nodes if node.node_type == node_type.value
            )

        brief = ResearchBrief(
            task_id=canonical_uuid(task.task_id),
            question=task.question,
            status=task.status,
            confirmed_claims=tuple(
                claim for claim in claims if claim.status == CLAIM_CONFIRMED
            ),
            findings=of_type(EvidenceNodeType.STUDY_FINDING),
            blindspots=of_type(EvidenceNodeType.BLINDSPOT),
            dissents=of_type(EvidenceNodeType.DEBATE_CAPSULE),
            discriminating_studies=of_type(EvidenceNodeType.DISCRIMINATING_STUDY),
            # Retained, not removed. CLAUDE.md 5.3 forbids deleting these and the
            # brief is where the researcher actually sees that they exist.
            refuted_or_withdrawn=tuple(
                node
                for node in nodes
                if node.status not in ("active", "provisional")
            ),
            unadmitted_events=tuple(
                f"{event.event_type} ({event.status})"
                for event in events
                if event.status in NOT_ADMITTED
            ),
            absent_seats=tuple(
                str(event.payload.get("seat", "unknown"))
                for event in events
                if event.event_type == SEAT_UNAVAILABLE
            ),
            failed_phases=tuple(
                str(event.payload.get("phase", "unknown"))
                for event in events
                if event.event_type == PHASE_FAILED
            ),
            skipped_phases=tuple(
                str(event.payload.get("phase", "unknown"))
                for event in events
                if event.event_type == PHASE_SKIPPED
            ),
            paper_count=papers,
            independent_cluster_count=clusters,
            is_mental_health=looks_like_mental_health(task.question),
        )
        brief.limitations = _limitations(brief)
        return brief


__all__ = ["BriefNode", "ReportService", "ResearchBrief", "looks_like_mental_health"]
