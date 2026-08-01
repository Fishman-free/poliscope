from __future__ import annotations

from packages.evidence.contracts import ClaimType


class CausalUpgradePolicy:
    """Enforces that correlation-only evidence cannot support causal claims."""

    _ALLOWED_PAIRS: frozenset[tuple[str, ClaimType]] = frozenset(
        {
            ("correlational", ClaimType.CORRELATIONAL),
            ("correlational", ClaimType.BOUNDARY),
            ("experimental", ClaimType.CAUSAL),
            ("experimental", ClaimType.MECHANISM),
            ("quasi_experimental", ClaimType.CAUSAL),
            ("longitudinal", ClaimType.CORRELATIONAL),
            ("longitudinal", ClaimType.BOUNDARY),
            ("cross_sectional", ClaimType.CORRELATIONAL),
            ("cross_sectional", ClaimType.BOUNDARY),
            ("meta_analysis", ClaimType.CORRELATIONAL),
            ("meta_analysis", ClaimType.BOUNDARY),
            ("qualitative", ClaimType.MECHANISM),
            ("qualitative", ClaimType.BOUNDARY),
        }
    )

    @classmethod
    def is_allowed_claim(cls, evidence_design: str, claim_type: ClaimType) -> bool:
        return (evidence_design, claim_type) in cls._ALLOWED_PAIRS

    @classmethod
    def validate(cls, evidence_design: str, claim_type: ClaimType) -> str | None:
        if cls.is_allowed_claim(evidence_design, claim_type):
            return None
        return (
            f"Evidence design '{evidence_design}' cannot support "
            f"claim type '{claim_type.value}'; correlation ≠ causation."
        )
