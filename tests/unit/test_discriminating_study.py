from __future__ import annotations

from uuid import uuid4

from packages.evidence.blindspot_models import DiscriminatingStudy
from packages.evidence.contracts import EvidenceNodeType


def test_discriminating_study_is_evidence_artifact() -> None:
    study = DiscriminatingStudy(
        id=uuid4(),
        target_blindspot_ids=(uuid4(),),
        objective="test competing predictions",
        recommended_design="RCT",
        key_data=("longitudinal",),
        competing_predictions=("pred_a", "pred_b"),
        resolvable_blindspots=(uuid4(),),
        expected_information_gain=0.7,
    )
    assert study.node_type == EvidenceNodeType.DISCRIMINATING_STUDY
    assert study.artifact_type == "research_recommendation"


def test_discriminating_study_requires_two_predictions() -> None:
    import pytest
    with pytest.raises(ValueError):
        DiscriminatingStudy(
            id=uuid4(),
            target_blindspot_ids=(uuid4(),),
            objective="test",
            recommended_design="RCT",
            key_data=("x",),
            competing_predictions=("only_one",),
            resolvable_blindspots=(uuid4(),),
            expected_information_gain=0.5,
        )
