from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GraphConsistencyResult:
    claim_id: UUID
    no_contradictory_admitted: bool
    no_duplicate_lineage: bool
    passed: bool


def check_graph_consistency(
    claim_id: UUID,
    no_contradictory_admitted: bool = True,
    no_duplicate_lineage: bool = True,
) -> GraphConsistencyResult:
    passed = no_contradictory_admitted and no_duplicate_lineage
    return GraphConsistencyResult(
        claim_id=claim_id,
        no_contradictory_admitted=no_contradictory_admitted,
        no_duplicate_lineage=no_duplicate_lineage,
        passed=passed,
    )
