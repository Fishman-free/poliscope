"""Unit tests for the serializable evidence-lineage view (A1)."""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.lineage_view import LineageViewRow, build_lineage_view


def test_shared_dataset_merges_cluster_and_emits_link() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    rows = [
        LineageViewRow(a, title="A", dataset_id="ds-1"),
        LineageViewRow(b, title="B", dataset_id="ds-1"),
        LineageViewRow(c, title="C"),
    ]
    view = build_lineage_view(rows)
    assert view["paper_count"] == 3
    assert view["independent_cluster_count"] == 2
    dataset_links = [
        link for link in view["links"] if link["dep_type"] == "SAME_DATASET"
    ]
    assert len(dataset_links) == 1
    assert set(dataset_links[0]["source_ids"]) == {str(a), str(b)}
    assert dataset_links[0]["merges"] is True


def test_shared_team_does_not_merge_but_is_shown() -> None:
    a, b = uuid4(), uuid4()
    rows = [
        LineageViewRow(a, title="A", authors=("Jane Doe",)),
        LineageViewRow(b, title="B", authors=("Jane Doe",)),
    ]
    view = build_lineage_view(rows)
    # Shared authorship never collapses independent evidence.
    assert view["independent_cluster_count"] == 2
    team_links = [
        link
        for link in view["links"]
        if link["dep_type"] == "SAME_RESEARCH_TEAM"
    ]
    assert len(team_links) == 1
    assert team_links[0]["merges"] is False


def test_every_source_carries_a_cluster_index() -> None:
    a, b = uuid4(), uuid4()
    view = build_lineage_view([LineageViewRow(a), LineageViewRow(b)])
    indices = {source["cluster_index"] for source in view["sources"]}
    assert indices == {0, 1}


def test_empty_corpus_is_safe() -> None:
    view = build_lineage_view([])
    assert view["paper_count"] == 0
    assert view["independent_cluster_count"] == 0
    assert view["sources"] == []
    assert view["links"] == []
    assert view["clusters"] == []
