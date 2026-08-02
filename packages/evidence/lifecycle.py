from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


class ResurrectionConditionNotMet(Exception):
    """Raised when a quarantined node cannot be resurrected."""


@dataclass
class QuarantinedNode:
    node_id: UUID
    reason: str
    attacker: str
    missing_evidence: str
    resurrection_condition: str
    status: str = "quarantined"


def check_resurrection_conditions(
    node: QuarantinedNode, new_evidence: tuple[UUID, ...]
) -> bool:
    """Pure predicate: does ``new_evidence`` satisfy ``node``'s resurrection condition?

    MVP rule, shared by ``LifecycleService.resurrect`` and the council's
    ``run_evidence_exchange`` wiring: any non-empty new evidence counts as
    satisfying the recorded ``resurrection_condition`` text. The condition
    text itself is not machine-parsed (design spec 7: that would require a
    claim-matching model this MVP does not have) -- it stays visible on the
    ``QuarantinedNode`` for a human researcher to judge, per CLAUDE.md 8
    ("researchers control direction"). A node already resurrected cannot be
    resurrected again from this predicate's perspective.
    """
    return node.status == "quarantined" and bool(new_evidence)


@dataclass
class LifecycleService:
    _quarantined: dict[UUID, QuarantinedNode] = field(default_factory=dict)

    def quarantine(
        self,
        node_id: UUID,
        reason: str,
        attacker: str,
        missing_evidence: str,
        resurrection_condition: str,
    ) -> QuarantinedNode:
        node = QuarantinedNode(
            node_id=node_id,
            reason=reason,
            attacker=attacker,
            missing_evidence=missing_evidence,
            resurrection_condition=resurrection_condition,
        )
        self._quarantined[node_id] = node
        return node

    def resurrect(
        self, node_id: UUID, evidence_refs: tuple[UUID, ...]
    ) -> QuarantinedNode:
        node = self._quarantined.get(node_id)
        if node is None:
            raise KeyError(f"node {node_id} not quarantined")
        if not check_resurrection_conditions(node, evidence_refs):
            raise ResurrectionConditionNotMet(
                "resurrection requires new evidence satisfying the original condition"
            )
        updated = QuarantinedNode(
            node_id=node.node_id,
            reason=node.reason,
            attacker=node.attacker,
            missing_evidence=node.missing_evidence,
            resurrection_condition=node.resurrection_condition,
            status="resurrected",
        )
        self._quarantined[node_id] = updated
        return updated

    def node_exists(self, node_id: UUID) -> bool:
        return node_id in self._quarantined
