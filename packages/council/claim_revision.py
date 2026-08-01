from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from packages.evidence.contracts import ClaimRevision, ClaimStatus


@dataclass
class ClaimRepository:
    _revisions: dict[tuple[UUID, int], ClaimRevision] = field(default_factory=dict)
    _head: dict[UUID, int] = field(default_factory=dict)

    def add(self, revision: ClaimRevision) -> None:
        self._revisions[(revision.claim_id, revision.revision)] = revision
        self._head[revision.claim_id] = revision.revision

    def get(self, claim_id: UUID, revision: int) -> ClaimRevision | None:
        return self._revisions.get((claim_id, revision))

    def head(self, claim_id: UUID) -> ClaimRevision | None:
        rev = self._head.get(claim_id)
        if rev is None:
            return None
        return self._revisions.get((claim_id, rev))


def revise_claim(
    current: ClaimRevision,
    response_type: str,
    new_statement: str | None = None,
    new_scope: dict[str, object] | None = None,
    new_confidence: Decimal | None = None,
) -> ClaimRevision:
    """Append a new revision; original is preserved."""
    next_revision = current.revision + 1
    if response_type == "WITHDRAW":
        return ClaimRevision(
            claim_id=current.claim_id,
            revision=next_revision,
            statement=current.statement,
            claim_type=current.claim_type,
            scope=current.scope,
            confidence=current.confidence,
            falsification_condition=current.falsification_condition,
            supersedes_revision=current.revision,
            status=ClaimStatus.WITHDRAWN,
        )
    return ClaimRevision(
        claim_id=current.claim_id,
        revision=next_revision,
        statement=new_statement if new_statement is not None else current.statement,
        claim_type=current.claim_type,
        scope=new_scope if new_scope is not None else current.scope,
        confidence=new_confidence if new_confidence is not None else current.confidence,
        falsification_condition=current.falsification_condition,
        supersedes_revision=current.revision,
        status=(
            ClaimStatus.NARROWED
            if response_type == "NARROW"
            else ClaimStatus.SUPPORTED
        ),
    )
