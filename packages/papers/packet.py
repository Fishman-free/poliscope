from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from packages.kernel.contracts import FrozenDict

from .contracts import (
    AnalysisSpec,
    AvailabilityStatus,
    CitationAnchor,
    EffectEstimate,
    EvidenceLevel,
    PaperEvidencePacket,
    ParsedPage,
    ResearchArtifactStatus,
    SampleDescription,
    SourceVersion,
    StudyDesign,
    StudyFindingCandidate,
    StudyPacket,
    VariableSpec,
    VerificationStatus,
)
from .parser import PageText, locate_quote


def source_version_hash(source: dict[str, object]) -> str:
    from packages.kernel.contracts import thaw_for_serialization

    canonical = json.dumps(
        thaw_for_serialization(source), sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_evidence_level(has_full_text: bool, has_anchor: bool) -> EvidenceLevel:
    if has_full_text and has_anchor:
        return EvidenceLevel.A
    if has_full_text:
        return EvidenceLevel.B
    return EvidenceLevel.C


def build_packet(
    *,
    source: dict[str, object],
    pages: list[PageText],
    study_question: str,
    population: str,
    design: str,
    exposure_variable: str,
    outcome_variable: str,
    analysis_method: str,
    finding_statement: str,
    origin: str,
    effect_direction: str,
    exact_quote: str,
    extraction_agent: str,
    author_conclusions: tuple[str, ...] = (),
    author_limitations: tuple[str, ...] = (),
    data_availability: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED,
    code_availability: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED,
    preregistration: AvailabilityStatus = AvailabilityStatus.NOT_REPORTED,
) -> PaperEvidencePacket:
    """Assemble a PaperEvidencePacket from parsed PDF pages and model output."""
    version_hash = source_version_hash(source)
    source_id = uuid4()
    source_version = SourceVersion(
        id=uuid4(),
        source_id=source_id,
        version_hash=version_hash,
        created_at="",
    )

    page_objects = tuple(
        ParsedPage(page_number=p.page_number, text=p.text) for p in pages
    )
    has_full_text = len(page_objects) > 0
    anchor_page = locate_quote(pages, exact_quote) if pages else None
    has_anchor = anchor_page is not None

    anchor = CitationAnchor(
        source_version_id=source_version.id,
        source_version_hash=version_hash,
        section=None,
        page=anchor_page,
        locator=f"page {anchor_page}" if has_anchor else "unknown",
        exact_quote=exact_quote,
        extraction_agent=extraction_agent,
        verification_status=VerificationStatus.QUOTE_CONFIRMED
        if has_anchor
        else VerificationStatus.LOCATION_MISMATCH,
    )

    finding = StudyFindingCandidate(
        id=uuid4(),
        statement=finding_statement,
        origin=origin,
        effect=EffectEstimate(direction=effect_direction),
        anchors=(anchor,),
    )

    study = StudyPacket(
        id=uuid4(),
        research_question=study_question,
        sample=SampleDescription(population=population),
        design=(
            StudyDesign(design)
            if design in StudyDesign._value2member_map_
            else StudyDesign.OTHER
        ),
        variables=(
            VariableSpec(
                name=exposure_variable,
                construct_clarifier=exposure_variable,
                role="exposure",
                operationalization=exposure_variable,
            ),
            VariableSpec(
                name=outcome_variable,
                construct_clarifier=outcome_variable,
                role="outcome",
                operationalization=outcome_variable,
            ),
        ),
        analysis=AnalysisSpec(method=analysis_method),
        findings=(finding,),
        author_conclusions=author_conclusions,
        author_limitations=author_limitations,
        artifacts=ResearchArtifactStatus(
            data=data_availability,
            code=code_availability,
            preregistration=preregistration,
        ),
    )

    return PaperEvidencePacket(
        source=FrozenDict(source),
        source_version=source_version,
        studies=(study,),
        pages=page_objects,
        evidence_level=build_evidence_level(has_full_text, has_anchor),
    )
