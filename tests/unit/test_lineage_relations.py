"""Lineage vocabulary and its effect on the independent evidence count.

CLAUDE.md 7.4 names the dependencies that make two papers one piece of evidence
and, just as importantly, the one that does not: a shared research team.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.independence import cluster_evidence
from packages.evidence.lineage import (
    LINEAGE_DEPENDENCY_TYPES,
    MERGING_DEPENDENCIES,
)


def test_versions_and_same_dataset_count_as_one_cluster() -> None:
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2, s3],
        dependencies=(
            (s1, "SAME_DATASET", "add-health"),
            (s2, "SAME_DATASET", "add-health"),
            (s3, "SAME_DATASET", "add-health"),
        ),
    )
    assert result.paper_count == 3
    assert result.independent_cluster_count == 1


def test_same_team_alone_does_not_merge_evidence() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "SAME_RESEARCH_TEAM", "lab-7"),
            (s2, "SAME_RESEARCH_TEAM", "lab-7"),
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
    """The id keys a cached cluster, so order dependence would thrash it."""
    s1, s2 = uuid4(), uuid4()
    links = (
        (s1, "SAME_DATASET", "add-health"),
        (s2, "SAME_DATASET", "add-health"),
    )
    r1 = cluster_evidence(sources=[s1, s2], dependencies=links)
    r2 = cluster_evidence(sources=[s2, s1], dependencies=links)
    assert r1.cluster_id == r2.cluster_id


def test_every_merging_dependency_is_a_known_lineage_type() -> None:
    """A merge rule outside the vocabulary would silently never fire."""
    assert MERGING_DEPENDENCIES <= LINEAGE_DEPENDENCY_TYPES


def test_shared_authorship_is_recorded_but_never_merges() -> None:
    """It is a dependency worth showing; it is not the same evidence."""
    assert "SAME_RESEARCH_TEAM" in LINEAGE_DEPENDENCY_TYPES
    assert "SAME_RESEARCH_TEAM" not in MERGING_DEPENDENCIES
