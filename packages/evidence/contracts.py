from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from packages.kernel.contracts import ContractModel, FrozenDict


class EvidenceNodeType(StrEnum):
    RESEARCH_QUESTION = "ResearchQuestion"
    CLAIM = "Claim"
    SOURCE = "Source"
    STUDY_FINDING = "StudyFinding"
    CONSTRUCT = "Construct"
    CONTEXT = "Context"
    BLINDSPOT = "Blindspot"
    DEBATE_CAPSULE = "DebateCapsule"
    DISCRIMINATING_STUDY = "DiscriminatingStudy"
    DISSENT_CERTIFICATE = "DissentCertificate"


class EvidenceEdgeType(StrEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"
    CONFOUNDS = "CONFOUNDS"
    MEDIATES = "MEDIATES"
    MODERATES = "MODERATES"
    OPERATIONALIZES = "OPERATIONALIZES"
    DERIVED_FROM = "DERIVED_FROM"
    APPLIES_IN = "APPLIES_IN"
    EXPOSES = "EXPOSES"
    TESTS = "TESTS"


class ClaimType(StrEnum):
    CAUSAL = "causal"
    CORRELATIONAL = "correlational"
    MEASUREMENT = "measurement"
    BOUNDARY = "boundary"
    MECHANISM = "mechanism"
    NULL_RESULT = "null_result"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    NARROWED = "narrowed"
    WITHDRAWN = "withdrawn"
    QUARANTINED = "quarantined"


class ClaimRevision(ContractModel):
    claim_id: UUID
    revision: int
    statement: str
    claim_type: ClaimType
    scope: FrozenDict[str, object]
    confidence: Decimal
    falsification_condition: str
    supersedes_revision: int | None = None
    status: ClaimStatus = ClaimStatus.PROPOSED


class AdmissionDisposition(StrEnum):
    ADMIT = "ADMIT"
    SOURCE_ONLY = "SOURCE_ONLY"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    TOOL_LEAD_ONLY = "TOOL_LEAD_ONLY"
    QUARANTINE = "QUARANTINE"


class AdmissionDecision(ContractModel):
    disposition: AdmissionDisposition
    reasons: tuple[str, ...] = ()
    evidence_level: str | None = None


class ScientificEventCandidate(ContractModel):
    id: UUID
    task_id: UUID
    event_type: str
    payload: FrozenDict[str, object]
    evidence_level: str | None = None
    source_id: UUID | None = None
    study_id: UUID | None = None
    finding_id: UUID | None = None
    claim_id: UUID | None = None
