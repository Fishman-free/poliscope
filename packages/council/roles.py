from __future__ import annotations

from dataclasses import dataclass

from packages.council.contracts import Seat


@dataclass(frozen=True, slots=True)
class RoleSpec:
    seat: Seat
    display_name: str
    expertise: tuple[str, ...]
    priority_weight: float = 1.0


ROLE_SPECS: dict[Seat, RoleSpec] = {
    Seat.THEORY_BUILDER: RoleSpec(
        seat=Seat.THEORY_BUILDER,
        display_name="Theory Builder",
        expertise=("theory", "mechanism"),
    ),
    Seat.CAUSAL_SCIENTIST: RoleSpec(
        seat=Seat.CAUSAL_SCIENTIST,
        display_name="Causal Scientist",
        expertise=("causality", "identification"),
    ),
    Seat.MEASUREMENT_SCIENTIST: RoleSpec(
        seat=Seat.MEASUREMENT_SCIENTIST,
        display_name="Measurement Scientist",
        expertise=("measurement", "construct"),
    ),
    Seat.REPLICATION_SCIENTIST: RoleSpec(
        seat=Seat.REPLICATION_SCIENTIST,
        display_name="Replication Scientist",
        expertise=("replication", "precision"),
    ),
    Seat.BOUNDARY_SCIENTIST: RoleSpec(
        seat=Seat.BOUNDARY_SCIENTIST,
        display_name="Boundary Scientist",
        expertise=("boundary", "moderation"),
    ),
    Seat.ADVERSARY_FALSIFIER: RoleSpec(
        seat=Seat.ADVERSARY_FALSIFIER,
        display_name="Adversarial Falsifier",
        expertise=("falsification", "bias"),
    ),
    Seat.EVIDENCE_AUDITOR: RoleSpec(
        seat=Seat.EVIDENCE_AUDITOR,
        display_name="Evidence Auditor",
        expertise=("audit", "provenance"),
    ),
}
