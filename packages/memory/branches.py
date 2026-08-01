from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Branch:
    id: UUID
    parent_id: UUID | None
    claim_id: UUID
    condition_variable: str
    status: str = "proposed"


@dataclass
class BranchService:
    _branches: dict[UUID, Branch] = field(default_factory=dict)

    def fork(
        self,
        claim_id: UUID,
        condition_variable: str,
        parent_id: UUID | None = None,
    ) -> Branch:
        branch = Branch(
            id=uuid4(),
            parent_id=parent_id,
            claim_id=claim_id,
            condition_variable=condition_variable,
        )
        self._branches[branch.id] = branch
        return branch

    def merge(self, branch_ids: tuple[UUID, ...], condition_variable: str) -> Branch:
        if len(branch_ids) < 2:
            raise ValueError("merge requires at least two branches")
        if not condition_variable:
            raise ValueError("condition_variable required for merge")
        parent_id = branch_ids[0]
        return self.fork(uuid4(), condition_variable, parent_id=parent_id)
