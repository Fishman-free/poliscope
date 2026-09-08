"""Unit coverage for the pure adversarial-retrieval query generator.

Wiring-level coverage (that run_acquisition actually appends these queries to
the round) lives in tests/unit/test_run_acquisition_adversarial_retrieval.py;
this file checks the three-intent generation contract (the six-to-three
reduction is documented in packages/evidence/adversarial_retrieval.py).
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.adversarial_retrieval import adversarial_retrieval_queries


def test_generates_exactly_three_queries_per_claim() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="Love is a human necessity"
    )

    assert len(queries) == 3
    assert len(set(queries)) == 3  # all three intents are distinct strings


def test_queries_use_the_claim_statement_not_the_uuid() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="Love is a human necessity"
    )

    assert all("Love is a human necessity" in query for query in queries)
    assert all(str(claim_id) not in query for query in queries)


def test_covers_the_three_kept_reverse_intents() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(
        claim_id, statement="screen time and wellbeing"
    )
    joined = " ".join(queries)

    for intent in (
        "contradictory",
        "null result",
        "failed replication",
    ):
        assert intent in joined


def test_dropped_intents_are_gone() -> None:
    # The three intents other seats already search by role were removed in the
    # six-to-three reduction (fewer, stronger searches on the small host).
    queries = adversarial_retrieval_queries(
        uuid4(), statement="screen time and wellbeing"
    )
    joined = " ".join(queries)

    for dropped in (
        "alternative theory",
        "construct validity",
        "boundary condition",
    ):
        assert dropped not in joined


def test_different_statements_produce_different_queries() -> None:
    first = adversarial_retrieval_queries(uuid4(), statement="claim A")
    second = adversarial_retrieval_queries(uuid4(), statement="claim B")

    assert first != second


def test_falls_back_to_the_research_question() -> None:
    queries = adversarial_retrieval_queries(
        uuid4(), statement="", question="Is love a human necessity?"
    )

    assert all("Is love a human necessity?" in query for query in queries)


def test_empty_topic_still_emits_three_english_intents() -> None:
    queries = adversarial_retrieval_queries(uuid4())

    assert len(queries) == 3
    assert all("claim " not in query for query in queries)
