from __future__ import annotations

from uuid import uuid4

from packages.evidence.independence import cluster_evidence


def test_extension_of_merges_into_one_cluster() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "EXTENSION_OF"),
            (s2, "EXTENSION_OF"),
        ),
    )
    assert result.independent_cluster_count == 1


def test_overlapping_sample_merges() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "OVERLAPPING_SAMPLE"),
            (s2, "OVERLAPPING_SAMPLE"),
        ),
    )
    assert result.independent_cluster_count == 1


def test_pending_review_edge_not_counted_as_dependency() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "PENDING_REVIEW"),
            (s2, "PENDING_REVIEW"),
        ),
    )
    # PENDING_REVIEW is not a verified dependency
    assert result.independent_cluster_count == 2


def test_suite() -> None:
    test_extension_of_merges_into_one_cluster()
    test_overlapping_sample_merges()
    test_pending_review_edge_not_counted_as_dependency()
