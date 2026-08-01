from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.evidence.contracts import ClaimRevision


@dataclass(frozen=True, slots=True)
class DebateCapsule:
    common_ground: tuple[str, ...]
    strongest_support: tuple[UUID, ...]
    strongest_opposition: tuple[UUID, ...]
    hinge_variables: tuple[str, ...]
    boundary_conditions: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    falsification_conditions: tuple[str, ...]
    source_refs: tuple[UUID, ...]
    dissent_cert_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        required_fields = [
            self.common_ground,
            self.strongest_support,
            self.strongest_opposition,
            self.hinge_variables,
            self.boundary_conditions,
            self.unresolved_conflicts,
            self.falsification_conditions,
            self.source_refs,
        ]
        if not all(required_fields):
            raise ValueError(
                "DebateCapsule requires all dialectical fields"
            )


@dataclass(frozen=True, slots=True)
class DialecticalFold:
    original_claim: ClaimRevision
    capsule: DebateCapsule
    folded: bool = True


def fold_debate(
    claim: ClaimRevision, capsule: DebateCapsule
) -> DialecticalFold:
    """Append a DebateCapsule via Event Ledger; original Claim preserved."""
    return DialecticalFold(original_claim=claim, capsule=capsule)
