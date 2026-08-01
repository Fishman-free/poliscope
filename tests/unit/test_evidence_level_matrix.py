from __future__ import annotations

from packages.evidence.contracts import (
    AdmissionDisposition,
)
from packages.evidence.gate import MinimalEvidenceGate


def test_level_a_admits_with_source() -> None:
    from uuid import uuid4

    from packages.evidence.contracts import ScientificEventCandidate

    gate = MinimalEvidenceGate()
    candidate = ScientificEventCandidate(
        id=uuid4(),
        task_id=uuid4(),
        event_type="FINDING",
        payload={"claim_type": "correlational", "study_design": "cross_sectional"},
        evidence_level="A",
        source_id=uuid4(),
        finding_id=uuid4(),
    )
    decision = gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.ADMIT


def test_level_b_is_source_only() -> None:
    from uuid import uuid4

    from packages.evidence.contracts import ScientificEventCandidate

    gate = MinimalEvidenceGate()
    candidate = ScientificEventCandidate(
        id=uuid4(),
        task_id=uuid4(),
        event_type="FINDING",
        payload={"claim_type": "correlational", "study_design": "cross_sectional"},
        evidence_level="B",
        source_id=uuid4(),
    )
    decision = gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.SOURCE_ONLY


def test_level_c_is_discovery_only() -> None:
    from uuid import uuid4

    from packages.evidence.contracts import ScientificEventCandidate

    gate = MinimalEvidenceGate()
    candidate = ScientificEventCandidate(
        id=uuid4(),
        task_id=uuid4(),
        event_type="DISCOVERY",
        payload={},
        evidence_level="C",
    )
    decision = gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.DISCOVERY_ONLY


def test_level_d_is_tool_lead_only() -> None:
    from uuid import uuid4

    from packages.evidence.contracts import ScientificEventCandidate

    gate = MinimalEvidenceGate()
    candidate = ScientificEventCandidate(
        id=uuid4(),
        task_id=uuid4(),
        event_type="TOOL_LEAD",
        payload={},
        evidence_level="D",
    )
    decision = gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.TOOL_LEAD_ONLY


def test_correlation_to_causation_blocked() -> None:
    from uuid import uuid4

    from packages.evidence.contracts import ScientificEventCandidate

    gate = MinimalEvidenceGate()
    candidate = ScientificEventCandidate(
        id=uuid4(),
        task_id=uuid4(),
        event_type="FINDING",
        payload={"claim_type": "causal", "study_design": "cross_sectional"},
        evidence_level="A",
        source_id=uuid4(),
        finding_id=uuid4(),
        claim_id=uuid4(),
    )
    decision = gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.QUARANTINE


def test_suite() -> None:
    test_level_a_admits_with_source()
    test_level_b_is_source_only()
    test_level_c_is_discovery_only()
    test_level_d_is_tool_lead_only()
    test_correlation_to_causation_blocked()
