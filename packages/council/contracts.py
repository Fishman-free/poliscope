from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from packages.kernel.contracts import ContractModel


class Seat(StrEnum):
    THEORY_BUILDER = "theory_builder"
    CAUSAL_SCIENTIST = "causal_scientist"
    MEASUREMENT_SCIENTIST = "measurement_scientist"
    REPLICATION_SCIENTIST = "replication_scientist"
    BOUNDARY_SCIENTIST = "boundary_scientist"
    ADVERSARY_FALSIFIER = "adversarial_falsifier"
    EVIDENCE_AUDITOR = "evidence_auditor"


ALL_SEATS: frozenset[Seat] = frozenset(Seat)


class ScientificActionType(StrEnum):
    PROPOSE = "PROPOSE"
    SUPPORT = "SUPPORT"
    CHALLENGE = "CHALLENGE"
    QUALIFY = "QUALIFY"
    FORK = "FORK"
    REQUEST = "REQUEST"
    REVISE = "REVISE"
    DISSENT = "DISSENT"


class ChallengeResponseType(StrEnum):
    DEFEND = "DEFEND"
    REVISE = "REVISE"
    NARROW = "NARROW"
    WITHDRAW = "WITHDRAW"
    DISSENT = "DISSENT"


class ScientificAction(ContractModel):
    actor: Seat
    action_type: ScientificActionType
    target_id: UUID | None = None
    statement: str
    evidence_refs: tuple[UUID, ...] = ()
    confidence: float = 0.5
    falsification_condition: str = ""
    novelty: str = ""


ALLOWED_ACTIONS: frozenset[ScientificActionType] = frozenset(ScientificActionType)
ALLOWED_RESPONSES: frozenset[ChallengeResponseType] = frozenset(ChallengeResponseType)
