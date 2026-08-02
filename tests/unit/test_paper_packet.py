from __future__ import annotations

from uuid import uuid4

import pytest

from packages.kernel.contracts import FrozenDict
from packages.papers.contracts import (
    AvailabilityStatus,
    EvidenceLevel,
    PaperEvidencePacket,
    StudyDesign,
    VerificationStatus,
)
from packages.papers.packet import (
    build_evidence_level,
    build_packet,
    source_version_hash,
)
from packages.papers.parser import PageText

_SOURCE_ID = uuid4()


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


def _build_valid_packet() -> PaperEvidencePacket:
    return build_packet(
        source_id=_SOURCE_ID,
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
        source_id=_SOURCE_ID,
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
        source_id=_SOURCE_ID,
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


def test_packet_source_version_id_is_derived_from_caller_supplied_source_id() -> None:
    """Regression: build_packet() used to mint its own disconnected uuid4()

    for source_id, so SourceVersion.source_id could never match a real,
    already-persisted SourceModel.id. The caller's id must now flow through
    untouched.
    """
    packet = _build_valid_packet()
    assert packet.source_version.source_id == _SOURCE_ID


def test_packet_ids_are_deterministic_across_replay() -> None:
    """CLAUDE.md 10: replaying the same source + quote must be idempotent,

    not mint a new node identity each time.
    """
    first = _build_valid_packet()
    second = _build_valid_packet()
    assert first.source_version.id == second.source_version.id
    assert first.studies[0].id == second.studies[0].id
    assert first.studies[0].findings[0].id == second.studies[0].findings[0].id


def test_packet_ids_differ_for_different_source_ids() -> None:
    """Two distinct sources must not collide on the same derived node id."""
    other_source_id = uuid4()
    other = build_packet(
        source_id=other_source_id,
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
    )
    baseline = _build_valid_packet()
    assert other.source_version.id != baseline.source_version.id
    assert other.studies[0].findings[0].id != baseline.studies[0].findings[0].id
