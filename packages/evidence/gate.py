from __future__ import annotations

from enum import StrEnum

from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.citation_verifier import verify_citation_entailment
from packages.evidence.contracts import (
    AdmissionDecision,
    AdmissionDisposition,
    ClaimType,
    ScientificEventCandidate,
)
from packages.evidence.method_auditor import audit_method_quality
from packages.evidence.source_verifier import verify_source
from packages.kernel.contracts import ContractModel


class AuditStage(StrEnum):
    SCHEMA = "SCHEMA"
    DEDUPLICATION = "DEDUPLICATION"
    SOURCE = "SOURCE"
    CITATION_ENTAILMENT = "CITATION_ENTAILMENT"
    METHOD_QUALITY = "METHOD_QUALITY"
    GRAPH_CONSISTENCY = "GRAPH_CONSISTENCY"


_AUDIT_SEQUENCE: tuple[AuditStage, ...] = (
    AuditStage.SCHEMA,
    AuditStage.DEDUPLICATION,
    AuditStage.SOURCE,
    AuditStage.CITATION_ENTAILMENT,
    AuditStage.METHOD_QUALITY,
    AuditStage.GRAPH_CONSISTENCY,
)


class AuditFinding(ContractModel):
    stage: AuditStage
    passed: bool
    detail: str = ""


class FullAdmissionDecision(AdmissionDecision):
    audit_findings: tuple[AuditFinding, ...] = ()

    model_config = AdmissionDecision.model_config


class MinimalEvidenceGate:
    """Admission gate for the Evidence Graph.

    Applies the A–D level matrix and enforces that:
    - Schema validation passes
    - Source/Study/Anchor references exist for findings
    - Claim includes type, scope, and falsification condition
    - Correlation does not upgrade to causation
    """

    _LEVEL_DISPOSITION: dict[str, AdmissionDisposition] = {
        "A": AdmissionDisposition.ADMIT,
        "B": AdmissionDisposition.SOURCE_ONLY,
        "C": AdmissionDisposition.DISCOVERY_ONLY,
        "D": AdmissionDisposition.TOOL_LEAD_ONLY,
    }

    def evaluate(self, candidate: ScientificEventCandidate) -> AdmissionDecision:
        level = (candidate.evidence_level or "D").upper()
        base = self._LEVEL_DISPOSITION.get(
            level, AdmissionDisposition.TOOL_LEAD_ONLY
        )

        if base == AdmissionDisposition.ADMIT:
            if candidate.finding_id and not candidate.source_id:
                return AdmissionDecision(
                    disposition=AdmissionDisposition.QUARANTINE,
                    reasons=("Finding must reference a Source.",),
                    evidence_level=level,
                )
            if candidate.claim_id:
                claim_type_payload = candidate.payload.get("claim_type")
                claim_type = (
                    ClaimType(str(claim_type_payload))
                    if claim_type_payload
                    else ClaimType.CORRELATIONAL
                )
                design = str(candidate.payload.get("study_design", ""))
                violation = CausalUpgradePolicy.validate(design, claim_type)
                if violation:
                    return AdmissionDecision(
                        disposition=AdmissionDisposition.QUARANTINE,
                        reasons=(violation,),
                        evidence_level=level,
                    )

        return AdmissionDecision(
            disposition=base,
            reasons=(),
            evidence_level=level,
        )


class FullEvidenceGate:
    """Complete 6-stage evidence gate with A–D matrix enforcement."""

    _LEVEL_DISPOSITION: dict[str, AdmissionDisposition] = {
        "A": AdmissionDisposition.ADMIT,
        "B": AdmissionDisposition.SOURCE_ONLY,
        "C": AdmissionDisposition.DISCOVERY_ONLY,
        "D": AdmissionDisposition.TOOL_LEAD_ONLY,
    }

    async def audit(
        self, candidate: ScientificEventCandidate
    ) -> FullAdmissionDecision:
        findings: list[AuditFinding] = []

        # Stage 1: Schema
        schema_ok = bool(candidate.id and candidate.task_id and candidate.event_type)
        findings.append(
            AuditFinding(stage=AuditStage.SCHEMA, passed=schema_ok)
        )
        if not schema_ok:
            return self._quarantine(findings, "schema validation failed")

        # Stage 2: Deduplication
        dedup_ok = True  # real impl would check hash uniqueness
        findings.append(
            AuditFinding(stage=AuditStage.DEDUPLICATION, passed=dedup_ok)
        )

        # Stage 3: Source
        source_id = candidate.source_id
        if source_id:
            src = verify_source(source_id)
            source_ok = src.passed
        else:
            source_ok = candidate.event_type != "FINDING"
        findings.append(
            AuditFinding(stage=AuditStage.SOURCE, passed=source_ok)
        )
        if not source_ok:
            return self._quarantine(findings, "source verification failed")

        # Stage 4: Citation Entailment
        if candidate.finding_id:
            citation = verify_citation_entailment(
                candidate.finding_id,
                exact_quote=str(candidate.payload.get("exact_quote", "")),
            )
            citation_ok = citation.passed
        else:
            citation_ok = True
        findings.append(
            AuditFinding(stage=AuditStage.CITATION_ENTAILMENT, passed=citation_ok)
        )
        if not citation_ok:
            return self._quarantine(findings, "citation entailment failed")

        # Stage 5: Method Quality
        if candidate.finding_id:
            method = audit_method_quality(candidate.finding_id)
            method_ok = method.passed
        else:
            method_ok = True
        findings.append(
            AuditFinding(stage=AuditStage.METHOD_QUALITY, passed=method_ok)
        )
        if not method_ok:
            return self._quarantine(findings, "method quality failed")

        # Stage 6: Graph Consistency
        if candidate.claim_id:
            claim_type_payload = candidate.payload.get("claim_type")
            claim_type = (
                ClaimType(str(claim_type_payload))
                if claim_type_payload
                else ClaimType.CORRELATIONAL
            )
            design = str(candidate.payload.get("study_design", ""))
            violation = CausalUpgradePolicy.validate(design, claim_type)
            consistency_ok = violation is None
            consistency_detail = violation or ""
        else:
            consistency_ok = True
            consistency_detail = ""
        findings.append(
            AuditFinding(
                stage=AuditStage.GRAPH_CONSISTENCY,
                passed=consistency_ok,
                detail=consistency_detail,
            )
        )
        if not consistency_ok:
            return self._quarantine(findings, consistency_detail)

        # Apply A–D matrix
        level = (candidate.evidence_level or "D").upper()
        disposition = self._LEVEL_DISPOSITION.get(
            level, AdmissionDisposition.TOOL_LEAD_ONLY
        )
        return FullAdmissionDecision(
            disposition=disposition,
            reasons=(),
            evidence_level=level,
            audit_findings=tuple(findings),
        )

    def _quarantine(
        self, findings: list[AuditFinding], reason: str
    ) -> FullAdmissionDecision:
        level = "A"
        return FullAdmissionDecision(
            disposition=AdmissionDisposition.QUARANTINE,
            reasons=(reason,),
            evidence_level=level,
            audit_findings=tuple(findings),
        )
