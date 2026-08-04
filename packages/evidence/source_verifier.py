from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SourceVerificationResult:
    source_id: UUID
    has_doi: bool
    has_title: bool
    has_authors: bool
    is_retracted: bool
    pdf_matches: bool
    is_uploaded: bool
    passed: bool


def verify_source(
    source_id: UUID,
    has_doi: bool = True,
    has_title: bool = True,
    has_authors: bool = True,
    is_retracted: bool = False,
    pdf_matches: bool = True,
    is_uploaded: bool = False,
) -> SourceVerificationResult:
    """Stage 3 (CLAUDE.md 7.3) authenticity check.

    An uploaded PDF structurally has no DOI, and ``_persist_uploaded``
    deliberately leaves ``title``/``authors`` empty rather than guessing at
    them (CLAUDE.md 7: an unknown must stay visibly unknown) -- nothing in
    the uploaded-PDF path ever backfills a title either, only ``dataset_id``
    (see ``FindingExtractor.extract_uploaded``). Requiring ``has_doi``,
    ``has_title``, and ``has_authors`` unconditionally would make every
    uploaded source permanently unadmittable, quarantining its Source node
    forever and leaving any later StudyFinding's ``DERIVED_FROM`` edge
    dangling. ``is_uploaded`` waives those three checks for a source
    acquired through ``PrivateObjectStore`` rather than a DOI lookup -- the
    real authenticity backstop for an upload is ``pdf_matches`` (the bytes
    themselves), not third-party metadata. Retraction status and
    pdf_matches stay mandatory regardless of how the source was acquired.
    """
    identified = has_doi or is_uploaded
    titled = has_title or is_uploaded
    known_provenance = has_authors or is_uploaded
    passed = (
        identified
        and titled
        and known_provenance
        and not is_retracted
        and pdf_matches
    )
    return SourceVerificationResult(
        source_id=source_id,
        has_doi=has_doi,
        has_title=has_title,
        has_authors=has_authors,
        is_retracted=is_retracted,
        pdf_matches=pdf_matches,
        is_uploaded=is_uploaded,
        passed=passed,
    )
