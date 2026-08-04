"""Turns persisted source rows into lineage dependency links.

``cluster_evidence`` (packages/evidence/independence.py) has always been able
to merge on any dependency type in ``MERGING_DEPENDENCIES``, but until now the
only link either caller ever produced was ``PREPRINT_VERSION_OF`` from a
shared canonical DOI -- ``sources`` had nowhere to persist an author list or a
dataset identifier, so ``SAME_DATASET`` and ``SAME_RESEARCH_TEAM`` could never
be detected. Two call sites (``packages/reports/service.py`` and
``apps/api/routers/workspace.py``) built the same DOI-only triple inline. This
module is the one place that logic lives now.

``dataset_id`` is wired for sources that reach the full-text stage: no
metadata-only adapter (OpenAlex, Crossref, Semantic Scholar, Unpaywall)
resolves a dataset identifier from a DOI lookup alone, so a Level B source
still has ``dataset_id = None``. Once ``packages.papers.finding_extraction``
fetches a source's open-access full text, it scans it with
``packages.papers.parser.detect_dataset_identifier`` for a known repository
accession pattern (ICPSR/OSF/Dataverse/Dryad/Zenodo) and writes a match back
onto that same ``sources`` row. ``SAME_DATASET`` links are therefore real for
any two Level A sources sharing a detected identifier, not only exercised by
unit tests with synthetic data -- though detection is limited to that named
pattern set, so an accession style outside it still reads as ``None`` rather
than a guess.

``authors`` is genuinely wired end to end: every adapter already parses it
onto ``NormalizedSource``, and ``SourceAcquisition._persist`` now saves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.evidence.independence import LineageLink


@dataclass(frozen=True, slots=True)
class LineageSourceRow:
    """The slice of a persisted ``Source`` that lineage detection needs."""

    source_id: UUID
    canonical_doi: str | None = None
    dataset_id: str | None = None
    authors: tuple[str, ...] = ()


def detect_lineage(rows: list[LineageSourceRow]) -> tuple[LineageLink, ...]:
    """Build lineage links from a task's source rows.

    Emits, per row:

    * ``PREPRINT_VERSION_OF`` keyed on ``canonical_doi``, when present.
    * ``SAME_DATASET`` keyed on ``dataset_id``, when present. Merging
      (CLAUDE.md 7.4).
    * ``SAME_RESEARCH_TEAM`` keyed on each individual author name
      (case-insensitive, whitespace-trimmed), one link per author. Never
      merges independent-evidence clusters -- ``cluster_evidence`` excludes it
      from ``MERGING_DEPENDENCIES`` on purpose, because two datasets from one
      lab are still two datasets (CLAUDE.md 4). It is surfaced so a consumer
      (e.g. the source-diversity check in a later phase) can still see the
      shared authorship.
    """
    links: list[LineageLink] = []
    for row in rows:
        if row.canonical_doi:
            links.append((row.source_id, "PREPRINT_VERSION_OF", row.canonical_doi))
        if row.dataset_id:
            links.append((row.source_id, "SAME_DATASET", row.dataset_id))
        for author in row.authors:
            normalized = author.strip().lower()
            if normalized:
                links.append((row.source_id, "SAME_RESEARCH_TEAM", normalized))
    return tuple(links)


__all__ = ["LineageSourceRow", "detect_lineage"]
