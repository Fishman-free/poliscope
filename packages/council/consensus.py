from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from packages.council.contracts import Seat


class ConsensusStatus(StrEnum):
    ADMITTED = "ADMITTED"
    CONDITIONAL = "CONDITIONAL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    claim_id: UUID
    status: ConsensusStatus
    unresolved_blockers: tuple[str, ...]
    supporting_seats: int = 0
    dissenting_seats: int = 0


def evaluate_consensus(
    claim_id: UUID,
    judgments: Mapping[Seat, str],
    has_unresolved_fatal_challenge: bool,
    evidence_refs: tuple[UUID, ...],
) -> ConsensusResult:
    blockers: list[str] = []
    if has_unresolved_fatal_challenge:
        blockers.append("unresolved fatal challenge")
    if not evidence_refs:
        blockers.append("no evidence refs")

    supporting = sum(1 for v in judgments.values() if v == "support")
    dissenting = sum(1 for v in judgments.values() if v == "dissent")

    status = (
        ConsensusStatus.BLOCKED
        if blockers
        else ConsensusStatus.ADMITTED
    )

    return ConsensusResult(
        claim_id=claim_id,
        status=status,
        unresolved_blockers=tuple(blockers),
        supporting_seats=supporting,
        dissenting_seats=dissenting,
    )
