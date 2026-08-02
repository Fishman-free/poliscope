"""``detect_lineage`` turns persisted source rows into lineage links.

CLAUDE.md 7.4 requires SAME_DATASET (merges) and SAME_RESEARCH_TEAM (does not
merge) to actually be detectable from what a Source row records, not just be
vocabulary that cluster_evidence knows how to consume.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.independence import cluster_evidence
from packages.evidence.lineage_detection import LineageSourceRow, detect_lineage


def test_shared_canonical_doi_produces_preprint_version_of() -> None:
    s1, s2 = uuid4(), uuid4()
    rows = [
        LineageSourceRow(source_id=s1, canonical_doi="10.1/x"),
        LineageSourceRow(source_id=s2, canonical_doi="10.1/x"),
    ]
    links = detect_lineage(rows)
    assert (s1, "PREPRINT_VERSION_OF", "10.1/x") in links
    assert (s2, "PREPRINT_VERSION_OF", "10.1/x") in links


def test_no_canonical_doi_emits_no_preprint_link() -> None:
    rows = [LineageSourceRow(source_id=uuid4(), canonical_doi=None)]
    assert detect_lineage(rows) == ()


def test_shared_dataset_id_merges_into_one_cluster() -> None:
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    rows = [
        LineageSourceRow(source_id=s1, dataset_id="add-health"),
        LineageSourceRow(source_id=s2, dataset_id="add-health"),
        LineageSourceRow(source_id=s3, dataset_id="different-dataset"),
    ]
    links = detect_lineage(rows)
    clustered = cluster_evidence([s1, s2, s3], links)
    assert clustered.paper_count == 3
    assert clustered.independent_cluster_count == 2


def test_shared_author_is_detected_but_never_merges_clusters() -> None:
    s1, s2 = uuid4(), uuid4()
    rows = [
        LineageSourceRow(source_id=s1, authors=("Jane Doe", "Alex Lee")),
        LineageSourceRow(source_id=s2, authors=("jane doe",)),
    ]
    links = detect_lineage(rows)
    assert (s1, "SAME_RESEARCH_TEAM", "jane doe") in links
    assert (s2, "SAME_RESEARCH_TEAM", "jane doe") in links
    clustered = cluster_evidence([s1, s2], links)
    assert clustered.independent_cluster_count == 2


def test_author_matching_is_case_and_whitespace_insensitive() -> None:
    s1, s2 = uuid4(), uuid4()
    rows = [
        LineageSourceRow(source_id=s1, authors=("  Jane Doe ",)),
        LineageSourceRow(source_id=s2, authors=("JANE DOE",)),
    ]
    links = detect_lineage(rows)
    assert links.count((s1, "SAME_RESEARCH_TEAM", "jane doe")) == 1
    assert links.count((s2, "SAME_RESEARCH_TEAM", "jane doe")) == 1


def test_blank_author_names_are_skipped() -> None:
    rows = [LineageSourceRow(source_id=uuid4(), authors=("   ", ""))]
    links = detect_lineage(rows)
    assert not any(link[1] == "SAME_RESEARCH_TEAM" for link in links)


def test_rows_with_no_lineage_signal_produce_no_links() -> None:
    rows = [LineageSourceRow(source_id=uuid4())]
    assert detect_lineage(rows) == ()


def test_combined_signals_from_one_row_all_surface() -> None:
    source_id = uuid4()
    rows = [
        LineageSourceRow(
            source_id=source_id,
            canonical_doi="10.1/x",
            dataset_id="add-health",
            authors=("Jane Doe",),
        )
    ]
    links = detect_lineage(rows)
    assert (source_id, "PREPRINT_VERSION_OF", "10.1/x") in links
    assert (source_id, "SAME_DATASET", "add-health") in links
    assert (source_id, "SAME_RESEARCH_TEAM", "jane doe") in links
    assert len(links) == 3
