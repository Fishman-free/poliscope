"""PostgreSQL-backed Graph Projector.

CLAUDE.md 5.3 names this module the only writer of the Evidence Graph. The
in-memory projector in :mod:`packages.evidence.projector` keeps a dictionary that
disappears with the process, so nothing the council concluded ever reached the
interface. This one reads the Scientific Event Ledger, runs each event through
the Evidence Gate, and writes the surviving nodes and edges under the
``poliscope_projector`` identity.

Three properties are load bearing:

* **Process events never become evidence.** Only an event whose type is one of
  the nine :class:`EvidenceNodeType` values can produce a node. A
  ``PHASE_STARTED`` or ``SEAT_TIMED_OUT`` event still reaches the ledger and the
  stream, because CLAUDE.md 5.1 wants the process visible, but it is not
  scientific evidence and the projector refuses to treat it as such.

* **Nothing is deleted.** A quarantined event keeps its ledger row and gains an
  audit row explaining the refusal. A refuted node changes ``status`` and stays.
  The migration withholds DELETE from every role, so this is enforced below the
  code as well as inside it.

* **Projection is idempotent and ordered.** A checkpoint records the highest
  sequence already handled, and an advisory lock keyed on the task means two
  workers cannot interleave. Re-running after a crash resumes rather than
  duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evidence.contracts import (
    AdmissionDisposition,
    EvidenceEdgeType,
    EvidenceNodeType,
    ScientificEventCandidate,
)
from packages.evidence.gate import FullEvidenceGate
from packages.evidence.models import (
    EventAuditModel,
    GraphEdgeModel,
    GraphNodeModel,
    ProjectionCheckpointModel,
    ScientificEventModel,
)
from packages.kernel.contracts import FrozenDict
from packages.kernel.database import canonical_uuid

# The nine formal node types from CLAUDE.md 5.2. An event type outside this set
# describes the process, not the evidence, and is deliberately not projected.
NODE_EVENT_TYPES: frozenset[str] = frozenset(item.value for item in EvidenceNodeType)

EDGE_TYPES: frozenset[str] = frozenset(item.value for item in EvidenceEdgeType)

# Ledger statuses. ``pending`` is what the council writes; the rest are verdicts
# only this module assigns.
STATUS_PENDING = "pending"
STATUS_ADMITTED = "admitted"
STATUS_QUARANTINED = "quarantined"
STATUS_PROCESS_ONLY = "process_only"

# Node statuses. A node is never removed, so every outcome is a status.
NODE_ACTIVE = "active"
NODE_PROVISIONAL = "provisional"

# Dispositions that record a lead rather than admissible evidence. CLAUDE.md 7.1
# forbids a Level C or D item from standing in for an original study, so these
# stay in the ledger and never reach the graph.
LEAD_ONLY_DISPOSITIONS: frozenset[AdmissionDisposition] = frozenset(
    {
        AdmissionDisposition.DISCOVERY_ONLY,
        AdmissionDisposition.TOOL_LEAD_ONLY,
    }
)


class ProjectionError(Exception):
    """Raised when an event cannot be projected for a structural reason."""


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    """One edge requested by an event payload."""

    target_node_id: UUID
    edge_type: str


@dataclass
class ProjectionReport:
    """What one projection pass did, for the worker and the audit trail."""

    task_id: UUID
    last_sequence: int = 0
    admitted: list[UUID] = field(default_factory=list)
    quarantined: list[UUID] = field(default_factory=list)
    leads: list[UUID] = field(default_factory=list)
    process_only: list[UUID] = field(default_factory=list)
    nodes_written: int = 0
    edges_written: int = 0

    @property
    def considered(self) -> int:
        return (
            len(self.admitted)
            + len(self.quarantined)
            + len(self.leads)
            + len(self.process_only)
        )


def node_id_for(event: ScientificEventModel) -> UUID:
    """Choose the identity a node keeps across replays.

    A claim referenced by three events must be one node, not three, so the
    domain id wins when the event carries one. Falling back to the event id is
    safe because ``uq_event_idempotency`` already makes that stable per replay.

    Not underscored: a quarantined event never reaches ``_upsert_node``, so
    ``apps/worker/jobs.py``'s Resurrect wiring reuses this exact resolution
    rule to name a quarantined node the same way it would have been named had
    it been admitted -- see ``_quarantined_nodes`` there.
    """
    declared = event.payload.get("node_id")
    if isinstance(declared, str):
        try:
            return UUID(declared)
        except ValueError as error:
            raise ProjectionError(
                f"event {event.id} declared an unparseable node_id {declared!r}"
            ) from error
    for candidate in (event.claim_id, event.finding_id, event.source_id):
        if candidate is not None:
            return candidate
    return event.id


def _edge_specs(event: ScientificEventModel) -> tuple[EdgeSpec, ...]:
    """Read the edges an event asks for, rejecting types outside CLAUDE.md 5.2."""
    raw = event.payload.get("edges")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProjectionError(f"event {event.id} has a non-list 'edges' payload")
    specs: list[EdgeSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ProjectionError(f"event {event.id} has a non-object edge entry")
        edge_type = str(item.get("type", ""))
        if edge_type not in EDGE_TYPES:
            raise ProjectionError(
                f"event {event.id} requested unknown edge type {edge_type!r}"
            )
        try:
            target = UUID(str(item.get("target")))
        except ValueError as error:
            raise ProjectionError(
                f"event {event.id} requested an edge to an unparseable target"
            ) from error
        specs.append(EdgeSpec(target_node_id=target, edge_type=edge_type))
    return tuple(specs)


def _optional_uuid(value: UUID | None) -> UUID | None:
    return None if value is None else canonical_uuid(value)


def _to_candidate(event: ScientificEventModel) -> ScientificEventCandidate:
    # canonical_uuid because asyncpg returns a UUID subclass that ContractModel
    # refuses; see packages.kernel.database for why the refusal is correct.
    return ScientificEventCandidate(
        id=canonical_uuid(event.id),
        task_id=canonical_uuid(event.task_id),
        event_type=event.event_type,
        payload=FrozenDict(event.payload),
        evidence_level=event.evidence_level,
        source_id=_optional_uuid(event.source_id),
        finding_id=_optional_uuid(event.finding_id),
        claim_id=_optional_uuid(event.claim_id),
    )


class SqlGraphConsistencyQuery:
    """The one production implementation of ``GraphConsistencyQuery``.

    Both checks are structural, not scientific (see gate.py's Stage 6
    docstring for why): ``existing_node_type`` catches a replay/id-collision
    where the same node_id is now being written as a different node_type, and
    ``duplicate_fork_exists`` catches the same dissent being recorded twice
    under two different Claim ids, not two genuinely distinct disagreements --
    CLAUDE.md 4 protects the latter, not the former.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def existing_node_type(self, node_id: UUID) -> str | None:
        node = await self._session.get(GraphNodeModel, node_id)
        return None if node is None else node.node_type

    async def duplicate_fork_exists(
        self, target_claim_id: UUID, statement: str, exclude_node_id: UUID
    ) -> bool:
        rows = await self._session.execute(
            select(GraphNodeModel.id, GraphNodeModel.payload).where(
                GraphNodeModel.node_type == EvidenceNodeType.CLAIM.value,
                GraphNodeModel.id != exclude_node_id,
            )
        )
        for _row_id, payload in rows.all():
            if payload.get("statement") != statement:
                continue
            edges = payload.get("edges")
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if (
                    isinstance(edge, dict)
                    and edge.get("type") == EvidenceEdgeType.CONTRADICTS.value
                    and edge.get("target") == str(target_claim_id)
                ):
                    return True
        return False


class SqlGraphProjector:
    """Projects admitted ledger events into ``graph_nodes`` and ``graph_edges``.

    One instance wraps one :class:`AsyncSession` opened as the projector role and
    does not commit, so the caller owns the transaction boundary. A pass that
    fails halfway leaves the checkpoint where it was and is simply retried.
    """

    def __init__(
        self,
        session: AsyncSession,
        gate: FullEvidenceGate | None = None,
    ) -> None:
        self._session = session
        self._gate = gate if gate is not None else FullEvidenceGate(
            graph_query=SqlGraphConsistencyQuery(session)
        )

    async def _lock_task(self, task_id: UUID) -> None:
        """Make this the only projector running for the task.

        Without it two workers would each read the same checkpoint and project
        the same events, and while ``uq_graph_edge`` would catch duplicate edges
        the node upserts would silently race.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"projector:{task_id}"},
        )

    async def checkpoint(self, task_id: UUID) -> int:
        value = await self._session.scalar(
            select(ProjectionCheckpointModel.last_sequence).where(
                ProjectionCheckpointModel.task_id == task_id
            )
        )
        return int(value or 0)

    async def _advance_checkpoint(self, task_id: UUID, sequence: int) -> None:
        row = await self._session.scalar(
            select(ProjectionCheckpointModel).where(
                ProjectionCheckpointModel.task_id == task_id
            )
        )
        if row is None:
            self._session.add(
                ProjectionCheckpointModel(
                    id=uuid4(), task_id=task_id, last_sequence=sequence
                )
            )
            return
        # Never moves backwards: a replay that re-reads old events must not
        # reopen them to a second projection.
        row.last_sequence = max(row.last_sequence, sequence)

    async def _record_audit(
        self,
        event_id: UUID,
        stage: str,
        decision: str,
        reasons: tuple[str, ...],
    ) -> None:
        self._session.add(
            EventAuditModel(
                id=uuid4(),
                event_id=event_id,
                gate_stage=stage,
                decision=decision,
                reasons={"reasons": list(reasons)},
            )
        )

    async def _upsert_node(
        self,
        node_id: UUID,
        task_id: UUID,
        node_type: str,
        payload: dict[str, object],
        status: str,
    ) -> bool:
        """Write the node, returning whether a new row was created.

        An existing node is updated rather than replaced because its id is
        referenced by edges and by the audit trail. CLAUDE.md 5.3 forbids
        removing it even when the council later refutes it.
        """
        existing = await self._session.get(GraphNodeModel, node_id)
        if existing is not None:
            existing.payload = payload
            existing.status = status
            return False
        self._session.add(
            GraphNodeModel(
                id=node_id,
                task_id=task_id,
                node_type=node_type,
                payload=payload,
                status=status,
            )
        )
        await self._session.flush()
        return True

    async def _write_edges(
        self,
        task_id: UUID,
        source_node_id: UUID,
        specs: tuple[EdgeSpec, ...],
    ) -> int:
        written = 0
        for spec in specs:
            target = await self._session.get(GraphNodeModel, spec.target_node_id)
            if target is None:
                # A dangling edge would let the interface draw a relationship to
                # evidence that was never admitted, which is exactly the kind of
                # unearned confidence CLAUDE.md 2 forbids.
                raise ProjectionError(
                    f"edge {spec.edge_type} points at unknown node "
                    f"{spec.target_node_id}"
                )
            duplicate = await self._session.scalar(
                select(GraphEdgeModel.id).where(
                    GraphEdgeModel.task_id == task_id,
                    GraphEdgeModel.source_node_id == source_node_id,
                    GraphEdgeModel.target_node_id == spec.target_node_id,
                    GraphEdgeModel.edge_type == spec.edge_type,
                )
            )
            if duplicate is not None:
                continue
            self._session.add(
                GraphEdgeModel(
                    id=uuid4(),
                    task_id=task_id,
                    source_node_id=source_node_id,
                    target_node_id=spec.target_node_id,
                    edge_type=spec.edge_type,
                )
            )
            written += 1
        await self._session.flush()
        return written

    async def _pending_events(
        self,
        task_id: UUID,
        after_sequence: int,
        limit: int | None,
    ) -> list[ScientificEventModel]:
        statement = (
            select(ScientificEventModel)
            .where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.sequence > after_sequence,
            )
            .order_by(ScientificEventModel.sequence)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def project_pending(
        self,
        task_id: UUID,
        limit: int | None = None,
    ) -> ProjectionReport:
        """Project every unprojected event for one task, in sequence order."""
        await self._lock_task(task_id)
        start = await self.checkpoint(task_id)
        report = ProjectionReport(task_id=task_id, last_sequence=start)
        for event in await self._pending_events(task_id, start, limit):
            await self._project_one(event, report)
            report.last_sequence = event.sequence
        if report.last_sequence > start:
            await self._advance_checkpoint(task_id, report.last_sequence)
        await self._session.flush()
        return report

    async def _project_one(
        self,
        event: ScientificEventModel,
        report: ProjectionReport,
    ) -> None:
        if event.event_type not in NODE_EVENT_TYPES:
            # CLAUDE.md 5.1: a process node does not automatically become formal
            # scientific evidence. It stays in the ledger and on the stream.
            event.status = STATUS_PROCESS_ONLY
            report.process_only.append(event.id)
            return

        decision = await self._gate.audit(_to_candidate(event))
        for finding in decision.audit_findings:
            await self._record_audit(
                event.id,
                finding.stage.value,
                "PASS" if finding.passed else "FAIL",
                (finding.detail,) if finding.detail else (),
            )

        if decision.disposition == AdmissionDisposition.QUARANTINE:
            event.status = STATUS_QUARANTINED
            report.quarantined.append(event.id)
            await self._record_audit(
                event.id, "ADMISSION", decision.disposition.value, decision.reasons
            )
            return

        if decision.disposition in LEAD_ONLY_DISPOSITIONS:
            event.status = decision.disposition.value.lower()
            report.leads.append(event.id)
            await self._record_audit(
                event.id, "ADMISSION", decision.disposition.value, decision.reasons
            )
            return

        # ADMIT and SOURCE_ONLY both produce a node. SOURCE_ONLY marks it
        # provisional so the interface can show that only metadata was verified
        # and the full text was never read.
        status = (
            NODE_ACTIVE
            if decision.disposition == AdmissionDisposition.ADMIT
            else NODE_PROVISIONAL
        )
        node_id = node_id_for(event)
        specs = _edge_specs(event)
        if await self._upsert_node(
            node_id,
            event.task_id,
            event.event_type,
            dict(event.payload),
            status,
        ):
            report.nodes_written += 1
        if (
            event.event_type == EvidenceNodeType.STUDY_FINDING.value
            and event.source_id is not None
        ):
            # CLAUDE.md 5.3 requires a StudyFinding to trace back to a Source, so
            # the lineage edge is created by the projector rather than trusted to
            # whichever seat happened to emit the event.
            specs = (
                *specs,
                EdgeSpec(event.source_id, EvidenceEdgeType.DERIVED_FROM.value),
            )
        report.edges_written += await self._write_edges(event.task_id, node_id, specs)
        event.status = STATUS_ADMITTED
        report.admitted.append(event.id)
        await self._record_audit(
            event.id, "ADMISSION", decision.disposition.value, decision.reasons
        )
