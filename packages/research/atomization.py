from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from packages.evidence.contracts import ClaimType
from packages.research.contracts import ResearchContract


@dataclass(frozen=True, slots=True)
class AtomicClaimCandidate:
    claim_id: UUID
    statement: str
    claim_type: ClaimType
    scope: dict[str, str]
    falsification_condition: str


def _primary_population(contract: ResearchContract) -> str:
    if contract.scope.populations:
        return contract.scope.populations[0]
    return "any"


def suggest_atomic_claims(
    contract: ResearchContract,
) -> tuple[AtomicClaimCandidate, ...]:
    pop = _primary_population(contract)
    return (
        AtomicClaimCandidate(
            claim_id=uuid4(),
            statement=f"关联主张：{contract.question}",
            claim_type=ClaimType.CORRELATIONAL,
            scope={"population": pop},
            falsification_condition="无显著相关性",
        ),
        AtomicClaimCandidate(
            claim_id=uuid4(),
            statement=f"因果主张：{contract.question}",
            claim_type=ClaimType.CAUSAL,
            scope={"population": pop},
            falsification_condition="随机实验无效应",
        ),
    )
