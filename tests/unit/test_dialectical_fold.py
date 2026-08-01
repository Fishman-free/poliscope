from __future__ import annotations

from uuid import uuid4

import pytest

from packages.evidence.contracts import ClaimRevision, ClaimStatus, ClaimType
from packages.evidence.dialectical_fold import (
    DebateCapsule,
    fold_debate,
)


def _make_revision(**over) -> ClaimRevision:
    base = dict(
        claim_id=uuid4(),
        revision=1,
        statement="social media affects mental health",
        claim_type=ClaimType.CORRELATIONAL,
        scope={"population": "adolescents"},
        confidence=0.7,
        falsification_condition="no association found",
        supersedes_revision=None,
        status=ClaimStatus.SUPPORTED,
    )
    base.update(over)
    return ClaimRevision(**base)


def test_debate_capsule_requires_all_fields() -> None:
    with pytest.raises((ValueError, TypeError)):
        DebateCapsule(common_ground=("association exists",))


def test_debate_capsule_complete_when_all_fields_present() -> None:
    capsule = DebateCapsule(
        common_ground=("association exists",),
        strongest_support=(uuid4(),),
        strongest_opposition=(uuid4(),),
        hinge_variables=("dosage",),
        boundary_conditions=("adolescents only",),
        unresolved_conflicts=("effect size varies",),
        falsification_conditions=("no association",),
        source_refs=(uuid4(),),
        dissent_cert_ids=(),
    )
    assert capsule.common_ground == ("association exists",)


def test_fold_debate_preserves_original_claim() -> None:
    original = _make_revision()
    capsule = DebateCapsule(
        common_ground=("x",),
        strongest_support=(uuid4(),),
        strongest_opposition=(uuid4(),),
        hinge_variables=("hv",),
        boundary_conditions=("bc",),
        unresolved_conflicts=("uc",),
        falsification_conditions=("fc",),
        source_refs=(uuid4(),),
        dissent_cert_ids=(),
    )
    folded = fold_debate(original, capsule)
    assert folded.original_claim == original
    assert folded.capsule == capsule
    assert folded.original_claim.status == ClaimStatus.SUPPORTED


def test_suite() -> None:
    test_debate_capsule_complete_when_all_fields_present()
    test_fold_debate_preserves_original_claim()
