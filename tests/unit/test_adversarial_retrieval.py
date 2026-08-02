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

    queries = adversarial_retrieval_queries(claim_id)

    assert len(queries) == 6
    assert len(set(queries)) == 6  # all six intents are distinct strings


def test_every_query_names_the_claim_id() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(claim_id)

    assert all(str(claim_id) in query for query in queries)


def test_covers_all_six_intents_from_design_spec_7_9() -> None:
    claim_id = uuid4()

    queries = adversarial_retrieval_queries(claim_id)
    joined = " ".join(queries)

    for intent in (
        "反驳",
        "零结果",
        "替代理论",
        "测量批评",
        "复现失败",
        "边界反转",
    ):
        assert intent in joined


def test_different_claims_produce_different_queries() -> None:
    first = adversarial_retrieval_queries(uuid4())
    second = adversarial_retrieval_queries(uuid4())

    assert first != second
