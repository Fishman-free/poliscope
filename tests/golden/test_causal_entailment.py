from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.contracts import ClaimType


def load_causal_cases() -> list[dict]:
    path = Path(__file__).parent / "fixtures" / "causal_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", load_causal_cases(), ids=lambda c: c["name"])
def test_forbidden_entailment_upgrades(case) -> None:
    claim_type = ClaimType(case["claim"])
    design = case["finding"]["design"]
    is_allowed = CausalUpgradePolicy.is_allowed_claim(design, claim_type)
    actual = "ALLOWED" if is_allowed else "BLOCKED"
    assert actual == case["expected"]


def test_suite() -> None:
    for case in load_causal_cases():
        test_forbidden_entailment_upgrades(case)
