from __future__ import annotations

from uuid import uuid4

from packages.evidence.contracts import (
    AdmissionDisposition,
    ScientificEventCandidate,
)
from packages.evidence.gate import AuditStage, FullEvidenceGate


def _candidate(**over) -> ScientificEventCandidate:
    base = dict(
        id=uuid4(),
        task_id=uuid4(),
        event_type="FINDING",
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


def test_suite() -> None:
    import asyncio
    asyncio.run(test_gate_records_required_stage_order())
    asyncio.run(test_full_gate_admits_valid_candidate())
    asyncio.run(test_full_gate_quarantines_correlation_causation())
    asyncio.run(test_full_gate_blocks_missing_source())
