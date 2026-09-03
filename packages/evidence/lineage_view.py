"""Serializable evidence-lineage view for the workbench (A1).

``detect_lineage`` + ``cluster_evidence`` already produce the independent
evidence count CLAUDE.md 7.4 requires, but only as two numbers. The Evidence
Lineage view needs the *structure* behind those numbers -- which papers share a
dataset, a cohort, a preprint/version chain, or merely a research team -- so the
researcher can see why N papers collapse into M independent evidence clusters.

This module is pure: it takes plain rows and returns JSON-able dicts, so the
clustering rule stays identical to ``_evidence_counts`` in the workspace router
and the shape is unit-testable without a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from packages.evidence.independence import cluster_evidence
from packages.evidence.lineage import MERGING_DEPENDENCIES
from packages.evidence.lineage_detection import LineageSourceRow, detect_lineage


@dataclass(frozen=True, slots=True)
class LineageViewRow:
    """Everything the lineage view shows about one persisted Source."""

    source_id: UUID
    title: str = ""
    doi: str | None = None
    canonical_doi: str | None = None
    dataset_id: str | None = None
    authors: tuple[str, ...] = ()
    publication_year: int | None = None


def build_lineage_view(rows: list[LineageViewRow]) -> dict[str, object]:
    """Return sources, dependency links, and independent clusters.

    ``links`` groups the raw ``(source, dep_type, group_key)`` triples by the
    thing they share: a link is only interesting when at least two sources
    share the same ``(dep_type, group_key)``. ``merges`` says whether that
    dependency collapses the independent-evidence count (SAME_RESEARCH_TEAM
    deliberately does not).
    """
    ordered_ids = [row.source_id for row in rows]
    dependencies = detect_lineage(
        [
            LineageSourceRow(
                source_id=row.source_id,
                canonical_doi=row.canonical_doi,
                dataset_id=row.dataset_id,
                authors=tuple(row.authors),
            )
            for row in rows
        ]
    )
    clustering = cluster_evidence(ordered_ids, dependencies)

    # Group dependency triples by (type, shared key) to build readable links.
    grouped: dict[tuple[str, str], set[UUID]] = defaultdict(set)
    for source_ref, dep_type, group_key in dependencies:
        grouped[(dep_type, group_key)].add(source_ref)

    links: list[dict[str, object]] = []
    for (dep_type, group_key), members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        links.append(
            {
                "dep_type": dep_type,
                "group_key": group_key,
                "merges": dep_type in MERGING_DEPENDENCIES,
                "source_ids": [str(identifier) for identifier in sorted(
                    members, key=lambda value: value.bytes
                )],
            }
        )

    # Map each source to its (stable, sorted) cluster index.
    cluster_of: dict[UUID, int] = {}
    clusters_out: list[dict[str, object]] = []
    ordered_clusters = sorted(
        clustering.clusters, key=lambda group: min(item.bytes for item in group)
    )
    for index, cluster in enumerate(ordered_clusters):
        member_ids = sorted(cluster, key=lambda value: value.bytes)
        clusters_out.append(
            {
                "cluster_index": index,
                "size": len(member_ids),
                "source_ids": [str(identifier) for identifier in member_ids],
            }
        )
        for identifier in member_ids:
            cluster_of[identifier] = index

    sources_out = [
        {
            "id": str(row.source_id),
            "title": row.title,
            "doi": row.doi,
            "authors": list(row.authors),
            "dataset_id": row.dataset_id,
            "publication_year": row.publication_year,
            "cluster_index": cluster_of.get(row.source_id),
        }
        for row in rows
    ]

    return {
        "paper_count": clustering.paper_count,
        "independent_cluster_count": clustering.independent_cluster_count,
        "sources": sources_out,
        "links": links,
        "clusters": clusters_out,
    }


__all__ = ["LineageViewRow", "build_lineage_view"]
