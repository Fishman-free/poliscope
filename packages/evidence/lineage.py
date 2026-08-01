from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LineageEdge:
    source_ref: UUID
    dependency_type: str
    verified: bool = True
    pending_review: bool = False


LINEAGE_DEPENDENCY_TYPES: frozenset[str] = frozenset(
    {
        "SAME_DATASET",
        "OVERLAPPING_SAMPLE",
        "SAME_RESEARCH_TEAM",
        "PREPRINT_VERSION_OF",
        "EXTENSION_OF",
        "REANALYSIS_OF",
        "CITES_WITHOUT_NEW_DATA",
        "META_ANALYSIS_INCLUDES",
    }
)

MERGING_DEPENDENCIES: frozenset[str] = frozenset(
    {
        "SAME_DATASET",
        "OVERLAPPING_SAMPLE",
        "PREPRINT_VERSION_OF",
        "EXTENSION_OF",
        "REANALYSIS_OF",
        "CITES_WITHOUT_NEW_DATA",
        "META_ANALYSIS_INCLUDES",
    }
)
