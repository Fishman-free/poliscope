"""Stage 6 (GRAPH_CONSISTENCY) with a real query wired in.

README known-gaps: GRAPH_CONSISTENCY used to be a hardcoded ``True`` that
never looked at the graph. These tests drive ``FullEvidenceGate`` with a fake
``GraphConsistencyQuery`` double (the real one is
``packages.evidence.sql_projector.SqlGraphConsistencyQuery``, exercised
against a real Postgres in ``tests/integration/test_sql_projector.py``) and
assert the two checks stay structural, not scientific: a legitimate,
distinct Fork against an existing claim must still be admitted -- CLAUDE.md 4
forbids silently rejecting dissent -- while a genuine node-id/type collision
or a literal duplicate Fork must be quarantined.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from packages.evidence.contracts import (
    AdmissionDisposition,
    EvidenceEdgeType,
    EvidenceNodeType,
    ScientificEventCandidate,
)
from packages.evidence.gate import AuditStage, FullEvidenceGate


class _FakeGraphConsistencyQuery:
    """In-memory double: no session, no SQL, just the two answers under test."""

    def __init__(
        self,
        *,
        existing_type: str | None = None,
        duplicate: bool = False,
    ) -> None:
        self._existing_type = existing_type
        self._duplicate = duplicate
        self.existing_node_type_calls: list[UUID] = []
        self.duplicate_fork_exists_calls: list[tuple[UUID, str, UUID]] = []

    async def existing_node_type(self, node_id: UUID) -> str | None:
        self.existing_node_type_calls.append(node_id)
        return self._existing_type

    async def duplicate_fork_exists(
        self, target_claim_id: UUID, statement: str, exclude_node_id: UUID
    ) -> bool:
        self.duplicate_fork_exists_calls.append(
            (target_claim_id, statement, exclude_node_id)
        )
        return self._duplicate


def _claim_candidate(**over: Any) -> ScientificEventCandidate:
    base: dict[str, Any] = dict(
        id=uuid4(),
        task_id=uuid4(),
        event_type=EvidenceNodeType.CLAIM.value,
        payload={
            "claim_type": "correlational",
            "study_design": "cross_sectional",
            "statement": "screen time correlates with anxiety",
        },
        evidence_level="A",
        claim_id=uuid4(),
    )
    base.update(over)
    return ScientificEventCandidate(**base)


def _fork_candidate(
    target_claim_id: UUID, statement: str, **over: Any
) -> ScientificEventCandidate:
    return _claim_candidate(
        payload={
            "claim_type": "correlational",
            "study_design": "cross_sectional",
            "statement": statement,
            "edges": [
                {
                    "type": EvidenceEdgeType.CONTRADICTS.value,
                    "target": str(target_claim_id),
                }
            ],
        },
        **over,
    )


async def test_no_graph_query_wired_is_unaffected() -> None:
    """Every existing caller that constructs FullEvidenceGate() bare must keep
    admitting a plain claim exactly as before -- backward compatibility."""
    gate = FullEvidenceGate()
    decision = await gate.audit(_claim_candidate())
    assert decision.disposition == AdmissionDisposition.ADMIT


async def test_node_id_collision_with_different_type_is_quarantined() -> None:
    """A real replay/id-collision: the same node_id is already in the graph
    but as a different node_type. This is structural corruption, not a
    scientific contradiction, so it must be caught."""
    query = _FakeGraphConsistencyQuery(existing_type=EvidenceNodeType.SOURCE.value)
    gate = FullEvidenceGate(graph_query=query)
    decision = await gate.audit(_claim_candidate())
    assert decision.disposition == AdmissionDisposition.QUARANTINE
    consistency = next(
        item
        for item in decision.audit_findings
        if item.stage == AuditStage.GRAPH_CONSISTENCY
    )
    assert consistency.passed is False
    assert "conflicting node_type" in consistency.detail


async def test_idempotent_replay_of_same_node_type_is_admitted() -> None:
    """The same node_id already existing as the *same* event_type is a replay,
    not a collision -- it must not be rejected."""
    query = _FakeGraphConsistencyQuery(existing_type=EvidenceNodeType.CLAIM.value)
    gate = FullEvidenceGate(graph_query=query)
    decision = await gate.audit(_claim_candidate())
    assert decision.disposition == AdmissionDisposition.ADMIT


async def test_legitimate_distinct_fork_is_still_admitted() -> None:
    """CLAUDE.md 4: dissent must not be silently deleted. A Fork producing a
    new Claim with a CONTRADICTS edge to an existing, distinct claim must be
    admitted -- this stage must never reject a CONTRADICTS edge on sight."""
    query = _FakeGraphConsistencyQuery(existing_type=None, duplicate=False)
    gate = FullEvidenceGate(graph_query=query)
    target = uuid4()
    decision = await gate.audit(
        _fork_candidate(target, "a genuinely distinct disagreement")
    )
    assert decision.disposition == AdmissionDisposition.ADMIT
    assert len(query.duplicate_fork_exists_calls) == 1
    called_target, called_statement, _ = query.duplicate_fork_exists_calls[0]
    assert called_target == target
    assert called_statement == "a genuinely distinct disagreement"


async def test_duplicate_fork_is_quarantined() -> None:
    """The same dissent (same target, same statement) forked twice is a
    duplicate lineage, not a second distinct disagreement, so it is caught."""
    query = _FakeGraphConsistencyQuery(existing_type=None, duplicate=True)
    gate = FullEvidenceGate(graph_query=query)
    target = uuid4()
    decision = await gate.audit(_fork_candidate(target, "already forked once"))
    assert decision.disposition == AdmissionDisposition.QUARANTINE
    consistency = next(
        item
        for item in decision.audit_findings
        if item.stage == AuditStage.GRAPH_CONSISTENCY
    )
    assert consistency.passed is False
    assert "already" in consistency.detail
