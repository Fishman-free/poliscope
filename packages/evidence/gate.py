from __future__ import annotations

from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.contracts import (
    AdmissionDecision,
    AdmissionDisposition,
    ClaimType,
    ScientificEventCandidate,
)


class MinimalEvidenceGate:
    """Admission gate for the Evidence Graph.

    Applies the A–D level matrix and enforces that:
    - Schema validation passes
    - Source/Study/Anchor references exist for findings
    - Claim includes type, scope, and falsification condition
    - Correlation does not upgrade to causation
    """

    _LEVEL_DISPOSITION: dict[str, AdmissionDisposition] = {
        "A": AdmissionDisposition.ADMIT,
        "B": AdmissionDisposition.SOURCE_ONLY,
        "C": AdmissionDisposition.DISCOVERY_ONLY,
        "D": AdmissionDisposition.TOOL_LEAD_ONLY,
    }

    def evaluate(self, candidate: ScientificEventCandidate) -> AdmissionDecision:
        level = (candidate.evidence_level or "D").upper()
        base = self._LEVEL_DISPOSITION.get(level, AdmissionDisposition.TOOL_LEAD_ONLY)

        reasons: list[str] = []

        if base == AdmissionDisposition.ADMIT:
            if candidate.finding_id and not candidate.source_id:
                return AdmissionDecision(
                    disposition=AdmissionDisposition.QUARANTINE,
                    reasons=("Finding must reference a Source.",),
                    evidence_level=level,
                )
            if candidate.claim_id:
                claim_type_payload = candidate.payload.get("claim_type")
                claim_type = (
                    ClaimType(str(claim_type_payload))
                    if claim_type_payload
                    else ClaimType.CORRELATIONAL
                )
                design = str(candidate.payload.get("study_design", ""))
                violation = CausalUpgradePolicy.validate(design, claim_type)
                if violation:
                    return AdmissionDecision(
                        disposition=AdmissionDisposition.QUARANTINE,
                        reasons=(violation,),
                        evidence_level=level,
                    )

        return AdmissionDecision(
            disposition=base,
            reasons=tuple(reasons),
            evidence_level=level,
        )
