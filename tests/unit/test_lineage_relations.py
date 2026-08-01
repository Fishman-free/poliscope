from __future__ import annotations

from uuid import uuid4

from packages.evidence.independence import (
    cluster_evidence,
)


def test_versions_and_same_dataset_count_as_one_cluster() -> None:
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2, s3],
        dependencies=(
            (s1, "SAME_DATASET"),
            (s2, "SAME_DATASET"),
            (s3, "SAME_DATASET"),
        ),
    )
    assert result.paper_count == 3
    assert result.independent_cluster_count == 1


def test_same_team_alone_does_not_merge_evidence() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "SAME_RESEARCH_TEAM"),
            (s2, "SAME_RESEARCH_TEAM"),
        ),
    )
    assert result.paper_count == 2
    assert result.independent_cluster_count == 2


def test_no_dependencies_means_all_independent() -> None:
    result = cluster_evidence(
        sources=[uuid4(), uuid4(), uuid4()],
        dependencies=(),
    )
    assert result.paper_count == 3
    assert result.independent_cluster_count == 3


def test_cluster_id_stable_regardless_of_input_order() -> None:
    s1, s2 = uuid4(), uuid4()
    r1 = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "SAME_DATASET"),
            (s2, "SAME_DATASET"),
        ),
    )
    r2 = cluster_evidence(
        sources=[s2, s1],
        dependencies=(
            (s1, "SAME_DATASET"),
            (s2, "SAME_DATASET"),
        ),
    )
    assert r1.cluster_id == r2.cluster_id


def test_suite() -> None:
    test_versions_and_same_dataset_count_as_one_cluster()
    test_same_team_alone_does_not_merge_evidence()
    test_no_dependencies_means_all_independent()
    test_cluster_id_stable_regardless_of_input_order()
