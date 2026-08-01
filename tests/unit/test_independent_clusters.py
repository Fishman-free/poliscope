"""Independent evidence clusters: papers counted are not evidence counted.

CLAUDE.md 7.4 requires the system to distinguish how many papers it read from
how many independent pieces of evidence it actually has. Over-merging hides real
corroboration; under-merging lets one dataset masquerade as five studies. Both
directions are tested.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.independence import cluster_evidence


def test_extension_of_merges_into_one_cluster() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "EXTENSION_OF", "cohort-a"),
            (s2, "EXTENSION_OF", "cohort-a"),
        ),
    )
    assert result.independent_cluster_count == 1


def test_overlapping_sample_merges() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "OVERLAPPING_SAMPLE", "panel-2019"),
            (s2, "OVERLAPPING_SAMPLE", "panel-2019"),
        ),
    )
    assert result.independent_cluster_count == 1


def test_two_different_datasets_stay_two_clusters() -> None:
    """Grouping on the dependency type alone collapsed these into one.

    Four papers over two distinct datasets are two pieces of evidence, not one.
    Reporting one would understate the corroboration a researcher actually has.
    """
    a1, a2, b1, b2 = uuid4(), uuid4(), uuid4(), uuid4()
    result = cluster_evidence(
        sources=[a1, a2, b1, b2],
        dependencies=(
            (a1, "SAME_DATASET", "add-health"),
            (a2, "SAME_DATASET", "add-health"),
            (b1, "SAME_DATASET", "millennium-cohort"),
            (b2, "SAME_DATASET", "millennium-cohort"),
        ),
    )
    assert result.paper_count == 4
    assert result.independent_cluster_count == 2


def test_a_shared_research_team_is_not_a_shared_dataset() -> None:
    """CLAUDE.md 7.4 lists team overlap as a dependency, not a merge rule."""
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "SAME_RESEARCH_TEAM", "lab-7"),
            (s2, "SAME_RESEARCH_TEAM", "lab-7"),
        ),
    )
    assert result.independent_cluster_count == 2


def test_pending_review_edge_not_counted_as_dependency() -> None:
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(
        sources=[s1, s2],
        dependencies=(
            (s1, "PENDING_REVIEW", "unknown"),
            (s2, "PENDING_REVIEW", "unknown"),
        ),
    )
    # PENDING_REVIEW is not a verified dependency
    assert result.independent_cluster_count == 2


def test_a_source_with_no_recorded_lineage_is_its_own_cluster() -> None:
    """Unknown lineage must not silently merge two papers into one."""
    s1, s2 = uuid4(), uuid4()
    result = cluster_evidence(sources=[s1, s2], dependencies=())
    assert result.independent_cluster_count == 2
