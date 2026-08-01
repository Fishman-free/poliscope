from __future__ import annotations

from uuid import uuid4

import pytest

from packages.evidence.contracts import (
    AdmissionDisposition,
    ScientificEventCandidate,
)
from packages.evidence.gate import MinimalEvidenceGate


@pytest.fixture
def minimal_gate() -> MinimalEvidenceGate:
    return MinimalEvidenceGate()


def _candidate(**overrides):
    base = {
        "id": uuid4(),
        "task_id": uuid4(),
        "event_type": "study_finding",
        "payload": {},
        "evidence_level": "A",
    }
    base.update(overrides)
    return ScientificEventCandidate.model_validate(base)


@pytest.mark.parametrize(
    "level,expected",
    [
        ("A", AdmissionDisposition.ADMIT),
        ("B", AdmissionDisposition.SOURCE_ONLY),
        ("C", AdmissionDisposition.DISCOVERY_ONLY),
        ("D", AdmissionDisposition.TOOL_LEAD_ONLY),
    ],
)
def test_minimal_gate_applies_level_matrix(level, expected, minimal_gate) -> None:
    decision = minimal_gate.evaluate(_candidate(evidence_level=level))
    assert decision.disposition == expected
    assert decision.evidence_level == level


def test_finding_without_source_is_quarantined(minimal_gate) -> None:
    candidate = _candidate(
        finding_id=uuid4(),
        source_id=None,
        evidence_level="A",
    )
    decision = minimal_gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.QUARANTINE


def test_causal_claim_from_correlational_evidence_is_quarantined(minimal_gate) -> None:
    candidate = _candidate(
        claim_id=uuid4(),
        evidence_level="A",
        payload={"claim_type": "causal", "study_design": "cross_sectional"},
    )
    decision = minimal_gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.QUARANTINE
    assert any(
        "causation" in r.lower() or "correlation" in r.lower()
        for r in decision.reasons
    )


def test_correlational_claim_from_correlational_is_admitted(
    minimal_gate,
) -> None:
    candidate = _candidate(
        claim_id=uuid4(),
        evidence_level="A",
        payload={"claim_type": "correlational", "study_design": "cross_sectional"},
    )
    decision = minimal_gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.ADMIT


def test_causal_claim_from_experimental_evidence_is_admitted(minimal_gate) -> None:
    candidate = _candidate(
        claim_id=uuid4(),
        evidence_level="A",
        payload={"claim_type": "causal", "study_design": "experimental"},
    )
    decision = minimal_gate.evaluate(candidate)
    assert decision.disposition == AdmissionDisposition.ADMIT
