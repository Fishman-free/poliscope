from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.contracts import ClaimType


def load_causal_cases() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "fixtures" / "causal_cases.json"
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return cases


@pytest.mark.parametrize("case", load_causal_cases(), ids=lambda c: c["name"])
def test_forbidden_entailment_upgrades(case: dict[str, Any]) -> None:
    claim_type = ClaimType(case["claim"])
    design = case["finding"]["design"]
    is_allowed = CausalUpgradePolicy.is_allowed_claim(design, claim_type)
    actual = "ALLOWED" if is_allowed else "BLOCKED"
    assert actual == case["expected"]
