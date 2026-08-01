"""Per-seat views of one shared evidence snapshot.

CLAUDE.md 3 requires the seven seats to share a runtime and a tool cache while
differing in evidence projection and questioning rules. This module is where that
difference is declared: same snapshot in, seven different orderings out.

Only two seats had a policy, so five of the seven fell back to an empty weight
map and saw an arbitrary order -- which made them, in the one respect that was
supposed to distinguish them, the same agent. All seven are defined below.
"""

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


THEORY_POLICY = ProjectionPolicy(
    seat="theory_builder",
    evidence_sort_weights={"mechanism": 1.0, "theory": 0.9, "experimental": 0.6},
    challenge_preferences=("mechanism_absent", "prediction_unfalsifiable"),
)

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

REPLICATION_POLICY = ProjectionPolicy(
    seat="replication_scientist",
    evidence_sort_weights={"replication": 1.0, "precision": 0.9, "experimental": 0.7},
    challenge_preferences=("underpowered", "null_read_as_absence"),
)

BOUNDARY_POLICY = ProjectionPolicy(
    seat="boundary_scientist",
    evidence_sort_weights={"context": 1.0, "moderation": 0.9, "population": 0.8},
    challenge_preferences=("overgeneralized_scope", "subgroup_as_whole"),
)

FALSIFIER_POLICY = ProjectionPolicy(
    seat="adversarial_falsifier",
    evidence_sort_weights={"contradiction": 1.0, "null_result": 0.9, "bias": 0.8},
    challenge_preferences=("alternative_explanation", "publication_bias"),
)

AUDITOR_POLICY = ProjectionPolicy(
    seat="evidence_auditor",
    evidence_sort_weights={"provenance": 1.0, "anchor": 0.9, "independence": 0.8},
    challenge_preferences=("missing_anchor", "duplicate_dataset"),
)

POLICIES: dict[str, ProjectionPolicy] = {
    policy.seat: policy
    for policy in (
        THEORY_POLICY,
        CAUSAL_POLICY,
        MEASUREMENT_POLICY,
        REPLICATION_POLICY,
        BOUNDARY_POLICY,
        FALSIFIER_POLICY,
        AUDITOR_POLICY,
    )
}


def get_policy_seats() -> set[str]:
    """The seats that have a declared projection. Should be all seven."""
    return set(POLICIES)
