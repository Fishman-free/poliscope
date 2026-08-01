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
    passed: bool


def verify_source(
    source_id: UUID,
    has_doi: bool = True,
    has_title: bool = True,
    has_authors: bool = True,
    is_retracted: bool = False,
    pdf_matches: bool = True,
) -> SourceVerificationResult:
    passed = (
        has_doi and has_title and has_authors and not is_retracted and pdf_matches
    )
    return SourceVerificationResult(
        source_id=source_id,
        has_doi=has_doi,
        has_title=has_title,
        has_authors=has_authors,
        is_retracted=is_retracted,
        pdf_matches=pdf_matches,
        passed=passed,
    )
