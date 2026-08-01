from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CitationEntailmentResult:
    finding_id: UUID
    exact_quote: str
    claim_entailed: bool
    qualifiers_preserved: bool
    passed: bool


def verify_citation_entailment(
    finding_id: UUID,
    exact_quote: str,
    claim_entailed: bool = True,
    qualifiers_preserved: bool = True,
) -> CitationEntailmentResult:
    passed = bool(exact_quote) and claim_entailed and qualifiers_preserved
    return CitationEntailmentResult(
        finding_id=finding_id,
        exact_quote=exact_quote,
        claim_entailed=claim_entailed,
        qualifiers_preserved=qualifiers_preserved,
        passed=passed,
    )
