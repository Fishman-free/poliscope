from __future__ import annotations

from typing import Any
from uuid import uuid4

from packages.evidence.contracts import (
    AdmissionDisposition,
    EvidenceNodeType,
    ScientificEventCandidate,
)
from packages.evidence.gate import AuditStage, FullEvidenceGate


def _candidate(**over: Any) -> ScientificEventCandidate:
    base: dict[str, Any] = dict(
        id=uuid4(),
        task_id=uuid4(),
        event_type=EvidenceNodeType.STUDY_FINDING.value,
        payload={
            "claim_type": "correlational",
            "study_design": "cross_sectional",
            "exact_quote": "significant association found",
        },
        evidence_level="A",
        source_id=uuid4(),
        finding_id=uuid4(),
    )
    base.update(over)
    return ScientificEventCandidate(**base)


async def test_gate_records_required_stage_order() -> None:
    gate = FullEvidenceGate()
    candidate = _candidate()
    decision = await gate.audit(candidate)
    stages = tuple(item.stage for item in decision.audit_findings)
    assert stages == (
        AuditStage.SCHEMA,
        AuditStage.DEDUPLICATION,
        AuditStage.SOURCE,
        AuditStage.CITATION_ENTAILMENT,
        AuditStage.METHOD_QUALITY,
        AuditStage.GRAPH_CONSISTENCY,
    )


async def test_full_gate_admits_valid_candidate() -> None:
    gate = FullEvidenceGate()
    candidate = _candidate()
    decision = await gate.audit(candidate)
    assert decision.disposition == AdmissionDisposition.ADMIT


async def test_full_gate_quarantines_correlation_causation() -> None:
    gate = FullEvidenceGate()
    candidate = _candidate(
        payload={"claim_type": "causal", "study_design": "cross_sectional"},
    )
    decision = await gate.audit(candidate)
    assert decision.disposition == AdmissionDisposition.QUARANTINE


async def test_full_gate_blocks_missing_source() -> None:
    gate = FullEvidenceGate()
    candidate = _candidate(source_id=None)
    decision = await gate.audit(candidate)
    assert decision.disposition == AdmissionDisposition.QUARANTINE


async def test_stage_source_itself_fails_for_sourceless_study_finding() -> None:
    """Regression: Stage 3 used to compare event_type against the literal
    string "FINDING", which never equals EvidenceNodeType.STUDY_FINDING's
    real value ("StudyFinding"). A StudyFinding event missing its source_id
    silently fell through to "not a finding, no source needed" and passed
    Stage 3 -- asserted here directly on the SOURCE finding, not just the
    final disposition, so a regression can't hide behind some other stage
    also happening to quarantine.
    """
    gate = FullEvidenceGate()
    candidate = _candidate(source_id=None)
    decision = await gate.audit(candidate)
    source_finding = next(
        item for item in decision.audit_findings if item.stage == AuditStage.SOURCE
    )
    assert source_finding.passed is False
