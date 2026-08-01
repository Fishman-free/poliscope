from __future__ import annotations


def test_all_council_seats_present() -> None:
    from packages.council.contracts import Seat
    assert len(Seat) == 7


def test_all_evidence_node_types_defined() -> None:
    from packages.evidence.contracts import EvidenceNodeType
    assert len(EvidenceNodeType) == 9


def test_all_evidence_edge_types_defined() -> None:
    from packages.evidence.contracts import EvidenceEdgeType
    assert len(EvidenceEdgeType) == 12
