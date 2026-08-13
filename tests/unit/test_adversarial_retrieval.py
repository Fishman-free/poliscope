"""Unit coverage for the pure adversarial-retrieval query generator.

Wiring-level coverage (that run_acquisition actually appends these queries to
the round) lives in tests/unit/test_run_acquisition_adversarial_retrieval.py;
this file only checks the six-intent generation contract design spec 7.9
describes (see packages/evidence/adversarial_retrieval.py for the honest
scope note on what these queries can and cannot do today).
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.adversarial_retrieval import adversarial_retrieval_queries


def test_generates_exactly_six_queries_per_claim() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="Love is a human necessity"
    )

    assert len(queries) == 6
    assert len(set(queries)) == 6  # all six intents are distinct strings


def test_queries_use_the_claim_statement_not_the_uuid() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="Love is a human necessity"
    )

    assert all("Love is a human necessity" in query for query in queries)
    assert all(str(claim_id) not in query for query in queries)


def test_covers_all_six_intents_from_design_spec_7_9() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="screen time and wellbeing"
    )
    joined = " ".join(queries)

    for intent in (
        "contradictory",
        "null result",
        "alternative theory",
        "construct validity",
        "failed replication",
        "boundary condition",
    ):
        assert intent in joined


def test_different_statements_produce_different_queries() -> None:
    first = adversarial_retrieval_queries(uuid4(), statement="claim A")
    second = adversarial_retrieval_queries(uuid4(), statement="claim B")

    assert first != second


def test_falls_back_to_the_research_question() -> None:
    queries = adversarial_retrieval_queries(
        uuid4(), statement="", question="Is love a human necessity?"
    )

    assert all("Is love a human necessity?" in query for query in queries)


def test_empty_topic_still_emits_six_english_intents() -> None:
    queries = adversarial_retrieval_queries(uuid4())

    assert len(queries) == 6
    assert all("claim " not in query for query in queries)
