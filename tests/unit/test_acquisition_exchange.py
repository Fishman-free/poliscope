from __future__ import annotations

from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.exchange import (
    EvidenceProjectionItem,
    ExchangeRound,
)
from packages.papers.candidate_pool import CandidatePool
from packages.papers.query_planner import QueryPlanner


async def test_duplicate_doi_is_one_candidate_with_two_requests() -> None:
    pool = CandidatePool()
    await pool.add(Seat.CAUSAL_SCIENTIST, "10.1234/EXAMPLE")
    await pool.add(Seat.EVIDENCE_AUDITOR, "https://doi.org/10.1234/example")
    seats = await pool.by_doi("10.1234/example")
    assert seats == frozenset({Seat.CAUSAL_SCIENTIST, Seat.EVIDENCE_AUDITOR})


async def test_exchange_contains_structured_projection_not_private() -> None:
    round_ = ExchangeRound()
    items = (
        EvidenceProjectionItem(
            source_id=uuid4(),
            study_id=uuid4(),
            anchor_summary="page 5",
            level="A",
        ),
    )
    result = await round_.run(items)
    assert result.evidence_items[0].source_id == items[0].source_id
    assert result.evidence_items[0].level == "A"


def test_query_planner_merges_requests() -> None:
    planner = QueryPlanner()
    merged = planner.merge_requests([
        (Seat.CAUSAL_SCIENTIST, "screen time"),
        (Seat.EVIDENCE_AUDITOR, "screen time"),
        (Seat.THEORY_BUILDER, "mechanism"),
    ])
    assert len(merged) == 2
