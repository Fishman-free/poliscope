from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
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


class GraphConsistencyQuery(Protocol):
    """Real graph lookups Stage 6 of ``FullEvidenceGate`` needs to answer the
    two booleans above with an actual query instead of a hardcoded default.

    Both checks are deliberately about **structural graph integrity**, not
    about scientific disagreement -- CLAUDE.md 4 forbids silently dropping
    dissent, and the Fork mechanism (``packages.council.rounds.registry``)
    exists specifically to produce a new ``Claim`` node carrying a
    ``CONTRADICTS`` edge to an existing admitted claim. If this stage rejected
    every ``CONTRADICTS`` edge outright, it would reject every legitimate Fork
    -- exactly the outcome CLAUDE.md 4 rules out. So:

    * ``existing_node_type`` answers "is this the same node being replayed
      (idempotent, fine), or has this id already been claimed by a different
      node type (a real replay/id-collision corruption)?" -- never look at
      edge types for this one.
    * ``duplicate_fork_exists`` answers "has this *exact* dissent (same
      target, same statement) already been forked once before?" -- a second,
      genuinely distinct Fork against the same target is untouched by this;
      only a literal duplicate of an already-recorded disagreement counts.

    No concrete session type is referenced here so ``packages.evidence.gate``
    stays free of SQLAlchemy imports; ``packages.evidence.sql_projector``
    supplies the one production implementation.
    """

    async def existing_node_type(self, node_id: UUID) -> str | None: ...

    async def duplicate_fork_exists(
        self, target_claim_id: UUID, statement: str, exclude_node_id: UUID
    ) -> bool: ...
