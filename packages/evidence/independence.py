from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from packages.evidence.lineage import MERGING_DEPENDENCIES

# (source_ref, dependency_type, group_key). The group key names the thing the
# two sources have in common: a dataset, a cohort, a canonical DOI.
LineageLink = tuple[UUID, str, str]


@dataclass(frozen=True, slots=True)
class ClusterResult:
    paper_count: int
    independent_cluster_count: int
    cluster_id: str
    clusters: tuple[frozenset[UUID], ...]


def cluster_evidence(
    sources: list[UUID],
    dependencies: tuple[LineageLink, ...],
) -> ClusterResult:
    """Group sources into independent evidence clusters.

    ``dependencies`` lists ``(source_ref, dep_type, group_key)`` triples. Two
    sources merge when they share both a dependency type in
    ``MERGING_DEPENDENCIES`` and the same ``group_key`` -- the dataset name, the
    cohort id, the canonical DOI, whatever the dependency is *about*.

    The group key is not decoration. Grouping on the type alone merged every
    source ever marked SAME_DATASET into a single cluster, so two studies of one
    dataset and two studies of a different dataset were reported as one piece of
    evidence instead of two, understating the independence CLAUDE.md 7.4 exists
    to measure.

    SAME_RESEARCH_TEAM never merges. Shared authorship is a dependency worth
    showing the researcher, but two datasets from one lab are still two
    datasets.
    """
    source_set = set(sources)
    parent: dict[UUID, UUID] = {s: s for s in sources}

    def find(x: UUID) -> UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: UUID, b: UUID) -> None:
        if a not in source_set or b not in source_set:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Group sources by (dependency type, what the dependency is about).
    by_key: dict[tuple[str, str], list[UUID]] = {}
    for source_ref, dep_type, group_key in dependencies:
        if dep_type not in MERGING_DEPENDENCIES:
            continue
        if source_ref in source_set:
            by_key.setdefault((dep_type, group_key), []).append(source_ref)

    for _key, group in by_key.items():
        if len(group) < 2:
            continue
        first = group[0]
        for other in group[1:]:
            union(first, other)

    cluster_map: dict[UUID, set[UUID]] = {}
    for s in sources:
        root = find(s)
        cluster_map.setdefault(root, set()).add(s)

    clusters = tuple(frozenset(group) for group in cluster_map.values())

    # Stable cluster ID from sorted UUIDs
    all_sorted = sorted(sources, key=lambda u: u.bytes)
    digest = hashlib.sha256(
        b"".join(u.bytes for u in all_sorted)
    ).hexdigest()[:16]

    return ClusterResult(
        paper_count=len(sources),
        independent_cluster_count=len(clusters),
        cluster_id=digest,
        clusters=clusters,
    )
