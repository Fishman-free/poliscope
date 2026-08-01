from __future__ import annotations

import pytest

from packages.kernel.contracts import FrozenDict
from packages.papers.contracts import (
    AvailabilityStatus,
    EvidenceLevel,
    StudyDesign,
    VerificationStatus,
)
from packages.papers.packet import (
    build_evidence_level,
    build_packet,
    source_version_hash,
)
from packages.papers.parser import PageText


def _source() -> dict[str, object]:
    return {
        "doi": "10.1234/example",
        "title": "Digital behavior and wellbeing",
        "provider_ids": {"openalex": "W123"},
    }


def _pages() -> list[PageText]:
    return [
        PageText(page_number=1, text="Introduction: digital behavior matters."),
        PageText(
            page_number=2,
            text="We found a significant association between screen time "
            "and anxiety.",
        ),
    ]


def _build_valid_packet():
    return build_packet(
        source=_source(),
        pages=_pages(),
        study_question="Does digital behavior affect mental health?",
        population="adolescents",
        design="longitudinal",
        exposure_variable="screen_time",
        outcome_variable="anxiety",
        analysis_method="linear regression",
        finding_statement="Screen time associates with anxiety.",
        origin="SOURCE_TEXT",
        effect_direction="positive",
        exact_quote="We found a significant association between screen "
        "time and anxiety.",
        extraction_agent="measurement_scientist",
        author_conclusions=("Screen time matters.",),
        author_limitations=("Self-reported.",),
        data_availability=AvailabilityStatus.RESTRICTED,
        code_availability=AvailabilityStatus.UNAVAILABLE,
        preregistration=AvailabilityStatus.NOT_REPORTED,
    )


def test_build_evidence_level_matrix() -> None:
    assert build_evidence_level(True, True) == EvidenceLevel.A
    assert build_evidence_level(True, False) == EvidenceLevel.B
    assert build_evidence_level(False, False) == EvidenceLevel.C


def test_source_version_hash_is_deterministic() -> None:
    source = _source()
    assert source_version_hash(source) == source_version_hash(dict(source))


def test_packet_contains_complete_study_method_and_reporting() -> None:
    packet = _build_valid_packet()
    assert packet.evidence_level == EvidenceLevel.A
    study = packet.studies[0]
    assert study.sample.population == "adolescents"
    assert study.design == StudyDesign.LONGITUDINAL
    assert {v.role for v in study.variables} >= {"exposure", "outcome"}
    assert study.analysis.method
    assert study.findings[0].effect.direction
    assert study.author_conclusions and study.author_limitations
    assert study.artifacts.data
    assert study.artifacts.code
    assert study.artifacts.preregistration


def test_packet_anchor_page_matches_exact_quote() -> None:
    packet = _build_valid_packet()
    anchor = packet.studies[0].findings[0].anchors[0]
    assert anchor.page == 2
    assert anchor.verification_status == VerificationStatus.QUOTE_CONFIRMED


def test_packet_missing_quote_downgrades_to_b() -> None:
    packet = build_packet(
        source=_source(),
        pages=_pages(),
        study_question="q",
        population="p",
        design="cross_sectional",
        exposure_variable="x",
        outcome_variable="y",
        analysis_method="t-test",
        finding_statement="s",
        origin="SOURCE_TEXT",
        effect_direction="null",
        exact_quote="quote not present in pdf",
        extraction_agent="agent",
    )
    assert packet.evidence_level == EvidenceLevel.B


def test_packet_source_version_hash_matches() -> None:
    packet = _build_valid_packet()
    expected = source_version_hash(packet.source)
    assert packet.source_version.version_hash == expected


def test_packet_source_payload_is_frozen_dict() -> None:
    packet = _build_valid_packet()
    assert isinstance(packet.source, FrozenDict)


def test_packet_immutability() -> None:
    packet = _build_valid_packet()
    with pytest.raises(TypeError):
        packet.source["extra"] = "x"  # type: ignore[index]


def test_packet_anchor_verification_mismatch_when_quote_missing() -> None:
    packet = build_packet(
        source=_source(),
        pages=[PageText(page_number=1, text="unrelated text")],
        study_question="q",
        population="p",
        design="cross_sectional",
        exposure_variable="x",
        outcome_variable="y",
        analysis_method="t-test",
        finding_statement="s",
        origin="SOURCE_TEXT",
        effect_direction="null",
        exact_quote="missing quote",
        extraction_agent="agent",
    )
    anchor = packet.studies[0].findings[0].anchors[0]
    assert anchor.page is None
    assert anchor.verification_status == VerificationStatus.LOCATION_MISMATCH
