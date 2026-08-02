"""Flags a claim's evidence base when every source traces to one dataset or team.

CLAUDE.md 7.4 and the design spec's `7.9` list a source-diversity constraint as
one of four mechanisms against a shared-error blindspot: several papers that
look like independent confirmation but in fact reuse one dataset (or come from
one research team) are, structurally, one piece of evidence dressed up as
several.

**Scope trim, stated plainly.** The design spec frames this per-claim: check
whether *a given claim's* supporting sources lack diversity. Nothing in the
current data model records which sources support which claim -- `Source` rows
are scoped to a task, not to a claim -- so a per-claim check is not buildable
without first adding that link. This module checks diversity across every
source acquired for the task within one ACQUISITION round instead, which is
real and end-to-end today: it reads the same `authors`/`dataset_id` columns
`packages/evidence/lineage_detection.py` uses, wired end to end in
`packages/papers/acquisition.py`. A future per-claim version needs a persisted
claim-to-source link first; that gap is recorded in README rather than
papered over.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SourceDiversityInput:
    """The slice of an acquired source this check needs."""

    source_id: UUID
    dataset_id: str | None = None
    authors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiversityFinding:
    reason: str
    source_ids: tuple[UUID, ...]


def check_diversity(sources: Sequence[SourceDiversityInput]) -> DiversityFinding | None:
    """Return a finding when every source shares one dataset or one author.

    Fewer than two sources cannot demonstrate a lack of diversity -- one source
    is just one source, not "sources that all look the same" -- so this
    returns ``None`` below that threshold.
    """
    if len(sources) < 2:
        return None

    dataset_ids = {source.dataset_id for source in sources}
    if len(dataset_ids) == 1 and None not in dataset_ids:
        (only,) = dataset_ids
        return DiversityFinding(
            reason=(
                f"全部 {len(sources)} 个来源共享同一数据集（{only}），"
                "不构成相互独立的证据。"
            ),
            source_ids=tuple(source.source_id for source in sources),
        )

    author_sets = [
        {author.strip().lower() for author in source.authors if author.strip()}
        for source in sources
    ]
    if all(author_sets):
        shared = set.intersection(*author_sets)
        if shared:
            example = sorted(shared)[0]
            return DiversityFinding(
                reason=(
                    f"全部 {len(sources)} 个来源共享至少一位作者"
                    f"（例如 {example}），可能来自同一研究团队而非独立复现。"
                ),
                source_ids=tuple(source.source_id for source in sources),
            )

    return None


__all__ = ["DiversityFinding", "SourceDiversityInput", "check_diversity"]
