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
        if not evidence_refs:
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
