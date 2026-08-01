from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from packages.kernel.contracts import ContractModel, FrozenDict


class StudyDesign(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"
    LONGITUDINAL = "longitudinal"
    EXPERIMENTAL = "experimental"
    QUASI_EXPERIMENTAL = "quasi_experimental"
    QUALITATIVE = "qualitative"
    META_ANALYSIS = "meta_analysis"
    OTHER = "other"


class AvailabilityStatus(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    NOT_REPORTED = "not_reported"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    QUOTE_CONFIRMED = "quote_confirmed"
    LOCATION_MISMATCH = "location_mismatch"
    PENDING_SECOND_PASS = "pending_second_pass"


class EvidenceLevel(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SampleDescription(ContractModel):
    size: int | None = None
    population: str
    recruitment: str | None = None
    regions: tuple[str, ...] = ()
    age_range: str | None = None


class VariableSpec(ContractModel):
    name: str
    construct_clarifier: str = Field(serialization_alias="construct")
    role: str
    operationalization: str


class AnalysisSpec(ContractModel):
    method: str
    estimand: str | None = None
    adjustments: tuple[str, ...] = ()
    sensitivity_analyses: tuple[str, ...] = ()


class EffectEstimate(ContractModel):
    direction: str
    measure: str | None = None
    value: Decimal | None = None
    uncertainty_type: str | None = None
    uncertainty_lower: Decimal | None = None
    uncertainty_upper: Decimal | None = None
    p_value: Decimal | None = None


class ResearchArtifactStatus(ContractModel):
    data: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED
    code: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED
    preregistration: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED
    urls: tuple[str, ...] = ()


class CitationAnchor(ContractModel):
    source_version_id: UUID
    source_version_hash: str
    section: str | None = None
    page: int | None = None
    locator: str
    exact_quote: str
    extraction_agent: str
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED


class StudyFindingCandidate(ContractModel):
    id: UUID
    statement: str
    origin: str
    effect: EffectEstimate
    anchors: tuple[CitationAnchor, ...]
    author_interpretation: str | None = None
    ai_derivation_creator: str | None = None


class StudyPacket(ContractModel):
    id: UUID
    research_question: str
    sample: SampleDescription
    design: StudyDesign
    variables: tuple[VariableSpec, ...]
    analysis: AnalysisSpec
    findings: tuple[StudyFindingCandidate, ...]
    author_conclusions: tuple[str, ...] = ()
    author_limitations: tuple[str, ...] = ()
    artifacts: ResearchArtifactStatus = ResearchArtifactStatus()


class SourceVersion(ContractModel):
    id: UUID
    source_id: UUID
    version_hash: str
    created_at: str


class ParsedPage(ContractModel):
    page_number: int
    text: str


class PaperEvidencePacket(ContractModel):
    source: FrozenDict[str, object]
    source_version: SourceVersion
    studies: tuple[StudyPacket, ...]
    pages: tuple[ParsedPage, ...] = ()
    evidence_level: EvidenceLevel = EvidenceLevel.D
