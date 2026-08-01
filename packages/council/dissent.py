from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat


@dataclass(frozen=True, slots=True)
class DissentCertificate:
    author: Seat
    target_id: UUID
    statement: str = ""
    reason: str = ""
    evidence_refs: tuple[UUID, ...] = ()
    withdrawal_condition: str = ""
    id: UUID = field(default_factory=uuid4)
    has_dissent: bool = True

    def __post_init__(self) -> None:
        if not self.statement or not self.reason:
            raise ValueError(
                "DissentCertificate requires statement and reason"
            )


def issue_dissent(
    author: Seat,
    target_id: UUID,
    statement: str,
    reason: str,
    evidence_refs: tuple[UUID, ...] = (),
    withdrawal_condition: str = "",
) -> DissentCertificate:
    return DissentCertificate(
        author=author,
        target_id=target_id,
        statement=statement,
        reason=reason,
        evidence_refs=evidence_refs,
        withdrawal_condition=withdrawal_condition,
    )
