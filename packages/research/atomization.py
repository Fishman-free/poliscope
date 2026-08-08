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
    if contract.task_type == "paper_review":
        # Round-7 deviation note (CLAUDE.md 17): ClaimType has no "review"
        # value, so the two review dimensions are mapped onto the closest
        # existing types -- MEASUREMENT for argument rigor (how constructs and
        # methods are measured), CORRELATIONAL for evidence sufficiency (what
        # the paper's conclusions actually rest on). These placeholders scope
        # the council's investigation; the concrete targets it attacks come
        # from the paper-understanding step's extracted main claims, not from
        # these two rows.
        return (
            AtomicClaimCandidate(
                claim_id=uuid4(),
                statement=f"论证严谨性：{contract.question}",
                claim_type=ClaimType.MEASUREMENT,
                scope={"population": pop},
                falsification_condition="论文存在不可修复的逻辑断裂或关键方法缺陷",
            ),
            AtomicClaimCandidate(
                claim_id=uuid4(),
                statement=f"证据充分性：{contract.question}",
                claim_type=ClaimType.CORRELATIONAL,
                scope={"population": pop},
                falsification_condition="论文主要结论缺乏可检索的证据支撑",
            ),
        )
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
