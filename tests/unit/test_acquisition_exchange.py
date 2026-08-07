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


async def test_doi_plus_chinese_justification_extracts_only_the_doi() -> None:
    """A seat often pastes a DOI in front of a justification sentence with no
    whitespace between them. The old split()[0] extraction took the whole
    tail as the DOI and the provider was queried with the entire sentence
    (an OpenAlex 404 like .../works/doi:10.1001/...%EF%BC%9A%E6%A0%B8%E6%9F%A5).
    The regex extraction must stop at the first non-DOI character (a full-width
    colon here)."""
    pool = CandidatePool()
    candidate = await pool.add(
        Seat.MEASUREMENT_SCIENTIST,
        "doi:10.1001/jamapediatrics.2013.4143：核对其中是否包含中国青少年样本、"
        "纳入各研究的横断面/纵向设计占比与混杂调整情况，并评估其识别假设"
        "（调整后无未测混杂、无反向因果、无选择偏倚）能否成立。",
    )
    assert candidate.normalized_doi == "10.1001/jamapediatrics.2013.4143"
    assert await pool.by_doi("10.1001/jamapediatrics.2013.4143") == frozenset(
        {Seat.MEASUREMENT_SCIENTIST}
    )


async def test_query_without_doi_stays_free_text() -> None:
    pool = CandidatePool()
    candidate = await pool.add(
        Seat.ADVERSARY_FALSIFIER,
        "中国青少年自杀倾向的纵向队列研究检索",
    )
    assert candidate.normalized_doi is None


async def test_doi_inside_doi_org_url_is_still_extracted() -> None:
    pool = CandidatePool()
    candidate = await pool.add(
        Seat.REPLICATION_SCIENTIST,
        "请查找 https://doi.org/10.1038/s41586-020-2649-2 的样本构成",
    )
    assert candidate.normalized_doi == "10.1038/s41586-020-2649-2"


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
