from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from packages.evidence.lineage import MERGING_DEPENDENCIES


@dataclass(frozen=True, slots=True)
class ClusterResult:
    paper_count: int
    independent_cluster_count: int
    cluster_id: str
    clusters: tuple[frozenset[UUID], ...]


def cluster_evidence(
    sources: list[UUID],
    dependencies: tuple[tuple[UUID, str], ...],
) -> ClusterResult:
    """Group sources into independent evidence clusters.

    ``dependencies`` lists ``(source_ref, dep_type)`` tuples. When two
    sources share the same verified dep_type in MERGING_DEPENDENCIES,
    they are merged into one cluster. SAME_RESEARCH_TEAM alone does
    NOT merge evidence.
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

    # Group sources by their verified dependency type
    by_type: dict[str, list[UUID]] = {}
    for source_ref, dep_type in dependencies:
        if dep_type not in MERGING_DEPENDENCIES:
            continue
        if source_ref in source_set:
            by_type.setdefault(dep_type, []).append(source_ref)

    for _dep_type, group in by_type.items():
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
