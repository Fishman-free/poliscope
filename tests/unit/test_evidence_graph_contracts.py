from __future__ import annotations

from packages.evidence.contracts import (
    ClaimRevision,
    ClaimStatus,
    ClaimType,
    EvidenceEdgeType,
    EvidenceNodeType,
)


def test_graph_contract_has_exactly_nine_nodes() -> None:
    assert len(EvidenceNodeType) == 9


def test_graph_contract_has_exactly_twelve_edges() -> None:
    assert len(EvidenceEdgeType) == 12


def test_graph_contract_contains_required_node_types() -> None:
    values = {member.value for member in EvidenceNodeType}
    assert "DiscriminatingStudy" in values
    assert "ResearchQuestion" in values
    assert "Claim" in values
    assert "Source" in values
    assert "StudyFinding" in values


def test_graph_contract_contains_required_edge_types() -> None:
    values = {member.value for member in EvidenceEdgeType}
    assert "DERIVED_FROM" in values
    assert "SUPPORTS" in values
    assert "REFUTES" in values
    assert "CONFOUNDS" in values
    assert "MEDIATES" in values
    assert "MODERATES" in values


def test_claim_revision_preserves_history() -> None:
    revision = ClaimRevision(
        claim_id=__import__("uuid").uuid4(),
        revision=1,
        statement="screen time affects wellbeing",
        claim_type=ClaimType.CORRELATIONAL,
        scope={},
        confidence=__import__("decimal").Decimal("0.5"),
        falsification_condition="null result in replication",
    )
    assert revision.status == ClaimStatus.PROPOSED
    assert revision.supersedes_revision is None
