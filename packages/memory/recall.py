from __future__ import annotations

from packages.memory.projection import (
    POLICIES,
    EvidenceProjection,
    ProjectionPolicy,
    RoleContext,
)


def perspective_recall(
    policy: ProjectionPolicy,
    process_recall: str,
    evidence_snapshot: list[dict[str, object]],
) -> RoleContext:
    """Produce a role-specific view of the same evidence snapshot.

    Different seats see the same evidence sorted by their own weights;
    no private process text leaks across seats.
    """
    weights = policy.evidence_sort_weights
    sorted_items = sorted(
        evidence_snapshot,
        key=lambda item: weights.get(item.get("type", ""), 0.0),
        reverse=True,
    )
    projection = EvidenceProjection(items=tuple(sorted_items))
    return RoleContext(
        process_recall=process_recall,
        evidence_projection=projection,
    )


def get_policy(seat: str) -> ProjectionPolicy:
    return POLICIES.get(
        seat,
        ProjectionPolicy(
            seat=seat,
            evidence_sort_weights={},
            challenge_preferences=(),
        ),
    )
