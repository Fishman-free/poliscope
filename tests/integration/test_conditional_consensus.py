from __future__ import annotations

from uuid import uuid4

from packages.council.consensus import (
    ConsensusStatus,
    evaluate_consensus,
)


def _six_support_one_dissent():
    from packages.council.contracts import Seat
    seats = list(Seat)
    judgments = {}
    for seat in seats[:-1]:
        judgments[seat] = "support"
    judgments[seats[-1]] = "dissent"
    return judgments


def test_six_supporters_cannot_override_failed_gate() -> None:
    result = evaluate_consensus(
        claim_id=uuid4(),
        judgments=_six_support_one_dissent(),
        has_unresolved_fatal_challenge=True,
        evidence_refs=(uuid4(),),
    )
    assert result.status != ConsensusStatus.ADMITTED
    assert result.unresolved_blockers


def test_consensus_requires_evidence_refs() -> None:
    result = evaluate_consensus(
        claim_id=uuid4(),
        judgments=_six_support_one_dissent(),
        has_unresolved_fatal_challenge=False,
        evidence_refs=(),
    )
    assert result.status != ConsensusStatus.ADMITTED


def test_consensus_admits_when_all_conditions_met() -> None:
    result = evaluate_consensus(
        claim_id=uuid4(),
        judgments={
            seat: "support"
            for seat in __import__(
                "packages.council.contracts", fromlist=["Seat"]
            ).Seat
        },
        has_unresolved_fatal_challenge=False,
        evidence_refs=(uuid4(),),
    )
    assert result.status == ConsensusStatus.ADMITTED


def test_suite() -> None:
    test_six_supporters_cannot_override_failed_gate()
    test_consensus_requires_evidence_refs()
    test_consensus_admits_when_all_conditions_met()
