"""Tests for the PostgreSQL Graph Projector.

These run against a real migrated database under the real projector role, so a
grant that is missing from the migration fails here rather than in production.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evidence.contracts import EvidenceEdgeType, EvidenceNodeType
from packages.evidence.models import (
    EventAuditModel,
    GraphEdgeModel,
    GraphNodeModel,
    ScientificEventModel,
)
from packages.evidence.sql_ledger import SqlEventLedger
from packages.evidence.sql_projector import (
    NODE_ACTIVE,
    NODE_PROVISIONAL,
    STATUS_ADMITTED,
    STATUS_PROCESS_ONLY,
    STATUS_QUARANTINED,
    ProjectionError,
    SqlGraphProjector,
)

CLAIM = EvidenceNodeType.CLAIM.value
SOURCE = EvidenceNodeType.SOURCE.value
FINDING = EvidenceNodeType.STUDY_FINDING.value

# A payload that satisfies every gate stage, so a test that expects a refusal
# only has to change the one field it is about.
CLEAN_FINDING: dict[str, Any] = {
    "exact_quote": "Effect sizes clustered between r = .03 and r = .09.",
    "has_doi": True,
    "has_title": True,
    "has_authors": True,
    "is_retracted": False,
    "pdf_matches": True,
}


async def _append(
    session: AsyncSession,
    task_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    key: str,
    *,
    level: str = "A",
    source_id: UUID | None = None,
    finding_id: UUID | None = None,
    claim_id: UUID | None = None,
) -> UUID:
    """Append one event through the app role, as the council would."""
    entry = await SqlEventLedger(session).append(
        task_id,
        event_type,
        payload,
        key,
        evidence_level=level,
        source_id=source_id,
        finding_id=finding_id,
        claim_id=claim_id,
    )
    await session.commit()
    return entry.event_id


async def _project(session: AsyncSession, task_id: UUID) -> Any:
    projector = SqlGraphProjector(session)
    report = await projector.project_pending(task_id)
    await session.commit()
    return report


async def _node_count(session: AsyncSession, task_id: UUID) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(GraphNodeModel)
        .where(GraphNodeModel.task_id == task_id)
    )
    return int(value or 0)


async def _event_status(session: AsyncSession, event_id: UUID) -> str:
    value = await session.scalar(
        select(ScientificEventModel.status).where(
            ScientificEventModel.id == event_id
        )
    )
    return str(value)


async def test_an_admitted_event_becomes_a_graph_node(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    await app_session.commit()
    claim_id = uuid4()
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {"statement": "Use correlates with distress", "claim_type": "correlational",
         "study_design": "correlational"},
        "claim-1",
        claim_id=claim_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.nodes_written == 1
    node = await projector_session.get(GraphNodeModel, claim_id)
    assert node is not None
    assert node.node_type == CLAIM
    assert node.status == NODE_ACTIVE


async def test_a_process_event_never_becomes_evidence(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 5.1: a process node is not automatically scientific evidence.

    The event still reaches the ledger and the stream, which is what the council
    view renders, but it must not appear in the Evidence Graph.
    """
    await app_session.commit()
    event_id = await _append(
        app_session, seeded_task, "PHASE_STARTED", {"phase": "PRECOMMITMENT"}, "p-1"
    )
    report = await _project(projector_session, seeded_task)

    assert report.process_only == [event_id]
    assert report.nodes_written == 0
    assert await _node_count(projector_session, seeded_task) == 0
    assert await _event_status(projector_session, event_id) == STATUS_PROCESS_ONLY


async def test_a_causal_claim_on_correlational_evidence_is_quarantined(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 3: correlation must not be upgraded into causation."""
    await app_session.commit()
    event_id = await _append(
        app_session,
        seeded_task,
        CLAIM,
        {"claim_type": "causal", "study_design": "cross_sectional"},
        "claim-causal",
        claim_id=uuid4(),
    )
    report = await _project(projector_session, seeded_task)

    assert report.quarantined == [event_id]
    assert await _node_count(projector_session, seeded_task) == 0
    assert await _event_status(projector_session, event_id) == STATUS_QUARANTINED


async def test_a_quarantined_event_keeps_its_row_and_gains_an_audit(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 5.3 forbids physical deletion, so refusal must leave a trail."""
    await app_session.commit()
    event_id = await _append(
        app_session,
        seeded_task,
        CLAIM,
        {"claim_type": "causal", "study_design": "cross_sectional"},
        "claim-causal-2",
        claim_id=uuid4(),
    )
    await _project(projector_session, seeded_task)

    assert await projector_session.get(ScientificEventModel, event_id) is not None
    reasons = await projector_session.scalars(
        select(EventAuditModel.reasons).where(EventAuditModel.event_id == event_id)
    )
    listed = [entry for entry in reasons if isinstance(entry, dict)]
    joined = " ".join(
        str(reason)
        for entry in listed
        for reason in cast(list[object], entry.get("reasons", []))
    )
    assert "causation" in joined


async def test_a_retracted_source_is_refused(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The gate read its own optimistic defaults before, admitting everything."""
    await app_session.commit()
    source_id = uuid4()
    event_id = await _append(
        app_session,
        seeded_task,
        SOURCE,
        {**CLEAN_FINDING, "is_retracted": True},
        "src-retracted",
        source_id=source_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.quarantined == [event_id]
    assert await projector_session.get(GraphNodeModel, source_id) is None


async def test_a_finding_without_an_exact_quote_is_refused(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 7.2 requires an anchor, so an unquoted finding is not evidence."""
    await app_session.commit()
    event_id = await _append(
        app_session,
        seeded_task,
        FINDING,
        {**CLEAN_FINDING, "exact_quote": ""},
        "finding-noquote",
        source_id=uuid4(),
        finding_id=uuid4(),
    )
    report = await _project(projector_session, seeded_task)

    assert report.quarantined == [event_id]


async def test_a_finding_gets_a_derived_from_edge_to_its_source(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Lineage is created by the projector, not trusted to the emitting seat."""
    await app_session.commit()
    source_id = uuid4()
    finding_id = uuid4()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "src-1",
        source_id=source_id,
    )
    await _append(
        app_session, seeded_task, FINDING, dict(CLEAN_FINDING), "finding-1",
        source_id=source_id, finding_id=finding_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.edges_written == 1
    edge = await projector_session.scalar(
        select(GraphEdgeModel).where(GraphEdgeModel.source_node_id == finding_id)
    )
    assert edge is not None
    assert edge.edge_type == EvidenceEdgeType.DERIVED_FROM.value
    assert edge.target_node_id == source_id


async def test_an_edge_to_an_unadmitted_node_is_refused(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """A dangling edge would draw a relationship to evidence that never passed."""
    await app_session.commit()
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": [
                {"target": str(uuid4()), "type": EvidenceEdgeType.SUPPORTS.value}
            ],
        },
        "claim-dangling",
        claim_id=uuid4(),
    )
    with pytest.raises(ProjectionError, match="unknown node"):
        await SqlGraphProjector(projector_session).project_pending(seeded_task)


async def test_an_unknown_edge_type_is_refused(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 5.2 fixes the twelve edge types; a thirteenth is a bug."""
    await app_session.commit()
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": [{"target": str(uuid4()), "type": "PROBABLY_RELATED"}],
        },
        "claim-badedge",
        claim_id=uuid4(),
    )
    with pytest.raises(ProjectionError, match="unknown edge type"):
        await SqlGraphProjector(projector_session).project_pending(seeded_task)


async def test_level_b_evidence_is_admitted_only_as_provisional(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 7.1: an abstract-only source cannot carry a full-text claim."""
    await app_session.commit()
    source_id = uuid4()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "src-b",
        level="B", source_id=source_id,
    )
    await _project(projector_session, seeded_task)

    node = await projector_session.get(GraphNodeModel, source_id)
    assert node is not None
    assert node.status == NODE_PROVISIONAL


async def test_level_d_evidence_stays_a_lead_and_never_enters_the_graph(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """A news mention is a lead. CLAUDE.md 7.1 forbids it standing in for a study."""
    await app_session.commit()
    event_id = await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "src-d",
        level="D", source_id=uuid4(),
    )
    report = await _project(projector_session, seeded_task)

    assert report.leads == [event_id]
    assert await _node_count(projector_session, seeded_task) == 0


async def test_projecting_twice_does_not_duplicate_anything(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """A crashed worker restarts, so a second pass must be a no-op."""
    await app_session.commit()
    source_id = uuid4()
    finding_id = uuid4()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "src-2",
        source_id=source_id,
    )
    await _append(
        app_session, seeded_task, FINDING, dict(CLEAN_FINDING), "finding-2",
        source_id=source_id, finding_id=finding_id,
    )
    first = await _project(projector_session, seeded_task)
    second = await _project(projector_session, seeded_task)

    assert first.nodes_written == 2
    assert second.considered == 0
    assert second.nodes_written == 0
    assert await _node_count(projector_session, seeded_task) == 2
    edges = await projector_session.scalar(
        select(func.count())
        .select_from(GraphEdgeModel)
        .where(GraphEdgeModel.task_id == seeded_task)
    )
    assert int(edges or 0) == 1


async def test_the_checkpoint_advances_and_resumes(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Resuming from the checkpoint is what makes a long task restartable."""
    await app_session.commit()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "cp-1",
        source_id=uuid4(),
    )
    await _project(projector_session, seeded_task)
    assert await SqlGraphProjector(projector_session).checkpoint(seeded_task) == 1

    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "cp-2",
        source_id=uuid4(),
    )
    report = await _project(projector_session, seeded_task)
    assert report.considered == 1
    assert await SqlGraphProjector(projector_session).checkpoint(seeded_task) == 2


async def test_events_are_projected_in_sequence_order(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """An edge can only reach a node an earlier event created."""
    await app_session.commit()
    source_id = uuid4()
    claim_id = uuid4()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "ord-src",
        source_id=source_id,
    )
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": [
                {"target": str(source_id), "type": EvidenceEdgeType.SUPPORTS.value}
            ],
        },
        "ord-claim",
        claim_id=claim_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.edges_written == 1
    assert report.last_sequence == 2


async def test_the_app_role_cannot_write_the_graph_itself(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The projector is the sole writer, enforced by GRANT rather than by habit."""
    from sqlalchemy.exc import ProgrammingError

    await app_session.commit()
    app_session.add(
        GraphNodeModel(
            id=uuid4(),
            task_id=seeded_task,
            node_type=CLAIM,
            payload={},
            status=NODE_ACTIVE,
        )
    )
    with pytest.raises(ProgrammingError, match="permission denied"):
        await app_session.flush()
    await app_session.rollback()


async def test_the_projector_cannot_rewrite_an_event_payload(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """UPDATE is granted one column at a time, so a verdict cannot edit evidence."""
    from sqlalchemy.exc import ProgrammingError

    await app_session.commit()
    event_id = await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "immutable-1",
        source_id=uuid4(),
    )
    row = await projector_session.get(ScientificEventModel, event_id)
    assert row is not None
    row.payload = {"rewritten": True}
    with pytest.raises(ProgrammingError, match="permission denied"):
        await projector_session.flush()
    await projector_session.rollback()


async def test_admitted_events_are_marked_so_the_ledger_shows_the_verdict(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    await app_session.commit()
    event_id = await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "verdict-1",
        source_id=uuid4(),
    )
    await _project(projector_session, seeded_task)
    assert await _event_status(projector_session, event_id) == STATUS_ADMITTED


async def test_a_node_id_reused_as_a_different_type_is_quarantined(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """README known-gaps: GRAPH_CONSISTENCY used to never look at the graph.

    A real replay/id-collision -- the same id already admitted as a Source
    node, now arriving as a Claim event -- is structural corruption and must
    be caught by ``SqlGraphConsistencyQuery.existing_node_type``.
    """
    await app_session.commit()
    reused_id = uuid4()
    await _append(
        app_session, seeded_task, SOURCE, dict(CLEAN_FINDING), "collide-src",
        source_id=reused_id,
    )
    await _project(projector_session, seeded_task)
    node = await projector_session.get(GraphNodeModel, reused_id)
    assert node is not None and node.node_type == SOURCE

    event_id = await _append(
        app_session,
        seeded_task,
        CLAIM,
        {"claim_type": "correlational", "study_design": "correlational"},
        "collide-claim",
        claim_id=reused_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.quarantined == [event_id]
    node_after = await projector_session.get(GraphNodeModel, reused_id)
    assert node_after is not None and node_after.node_type == SOURCE


async def test_a_distinct_fork_against_an_admitted_claim_is_still_admitted(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """CLAUDE.md 4: dissent must not be silently deleted. A Fork producing a
    new Claim with a CONTRADICTS edge to an already-admitted claim must still
    be admitted -- GRAPH_CONSISTENCY must never reject a CONTRADICTS edge on
    sight.
    """
    await app_session.commit()
    original_id = uuid4()
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "statement": "Use correlates with distress",
            "claim_type": "correlational",
            "study_design": "correlational",
        },
        "fork-original",
        claim_id=original_id,
    )
    await _project(projector_session, seeded_task)

    fork_id = uuid4()
    event_id = await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "statement": "Use does not correlate with distress in this cohort",
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": [
                {
                    "target": str(original_id),
                    "type": EvidenceEdgeType.CONTRADICTS.value,
                }
            ],
        },
        "fork-distinct",
        claim_id=fork_id,
    )
    report = await _project(projector_session, seeded_task)

    assert report.admitted == [event_id]
    assert await projector_session.get(GraphNodeModel, fork_id) is not None


async def test_a_duplicate_fork_of_the_same_dissent_is_quarantined(
    app_session: AsyncSession,
    projector_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The same dissent (same target, same statement) forked twice under two
    different Claim ids is duplicate lineage, not a second distinct
    disagreement, so the second one is caught.
    """
    await app_session.commit()
    original_id = uuid4()
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "statement": "Use correlates with distress",
            "claim_type": "correlational",
            "study_design": "correlational",
        },
        "dup-original",
        claim_id=original_id,
    )
    await _project(projector_session, seeded_task)

    dissent_statement = "Use does not correlate with distress in this cohort"
    edges = [
        {"target": str(original_id), "type": EvidenceEdgeType.CONTRADICTS.value}
    ]
    await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "statement": dissent_statement,
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": edges,
        },
        "dup-fork-first",
        claim_id=uuid4(),
    )
    await _project(projector_session, seeded_task)

    duplicate_event_id = await _append(
        app_session,
        seeded_task,
        CLAIM,
        {
            "statement": dissent_statement,
            "claim_type": "correlational",
            "study_design": "correlational",
            "edges": edges,
        },
        "dup-fork-second",
        claim_id=uuid4(),
    )
    report = await _project(projector_session, seeded_task)

    assert report.quarantined == [duplicate_event_id]
