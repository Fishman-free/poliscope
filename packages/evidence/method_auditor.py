from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MethodQualityResult:
    finding_id: UUID
    directness: float
    design_quality: float
    measurement_quality: float
    precision: float
    replicability: float
    external_validity: float
    passed: bool


def audit_method_quality(
    finding_id: UUID,
    directness: float = 0.8,
    design_quality: float = 0.8,
    measurement_quality: float = 0.8,
    precision: float = 0.8,
    replicability: float = 0.8,
    external_validity: float = 0.8,
    threshold: float = 0.5,
) -> MethodQualityResult:
    passed = all(
        value >= threshold
        for value in (
            directness,
            design_quality,
            measurement_quality,
            precision,
            replicability,
            external_validity,
        )
    )
    return MethodQualityResult(
        finding_id=finding_id,
        directness=directness,
        design_quality=design_quality,
        measurement_quality=measurement_quality,
        precision=precision,
        replicability=replicability,
        external_validity=external_validity,
        passed=passed,
    )
