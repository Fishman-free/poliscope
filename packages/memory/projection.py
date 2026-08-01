from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectionPolicy:
    seat: str
    evidence_sort_weights: dict[str, float]
    challenge_preferences: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    items: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class RoleContext:
    process_recall: str
    evidence_projection: EvidenceProjection


CAUSAL_POLICY = ProjectionPolicy(
    seat="causal_scientist",
    evidence_sort_weights={"experimental": 1.0, "causal": 0.9},
    challenge_preferences=("confounding", "reverse_causation"),
)

MEASUREMENT_POLICY = ProjectionPolicy(
    seat="measurement_scientist",
    evidence_sort_weights={"measurement": 1.0, "construct": 0.9},
    challenge_preferences=("operationalization", "reliability"),
)

POLICIES: dict[str, ProjectionPolicy] = {
    "causal_scientist": CAUSAL_POLICY,
    "measurement_scientist": MEASUREMENT_POLICY,
}
