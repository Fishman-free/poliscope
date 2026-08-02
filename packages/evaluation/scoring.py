"""Code-computable ForesightBlindspot scores.

Design spec 11.3 lists Blindspot Recall/Precision, Citation Existence/
Entailment Accuracy, Evidence Independence Accuracy, and Causal Overclaim
Rate as the metrics a baseline comparison needs. Every function here reuses
the production rule it is scoring against -- :class:`CausalUpgradePolicy`,
:func:`verify_citation_entailment`, :func:`detect_lineage` /
:func:`cluster_evidence` -- rather than re-deriving the rule a second time,
so a baseline's score reflects the same standard the gated system is held
to, not a parallel approximation of it.

What this deliberately does NOT compute: agreement with a human annotator.
CLAUDE.md 7 forbids treating an unverified quantity as if it were measured,
and no human-labelled ground truth exists yet for any of these cases -- see
:mod:`packages.evaluation.agreement` for the explicitly incomplete Kappa/
Alpha skeleton this implies. ``score_blindspots``' keyword match against
``expected_blindspots`` is a coarse proxy for that missing human judgment,
not a replacement for it.
"""

from __future__ import annotations

from collections.abc import Iterable

from packages.council.rounds.registry import FINAL_JUDGMENT
from packages.evaluation.harness import BaselineOutcome
from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.citation_verifier import verify_citation_entailment
from packages.evidence.contracts import ClaimType, EvidenceNodeType
from packages.evidence.independence import cluster_evidence
from packages.evidence.ledger import LedgerEntry
from packages.evidence.lineage_detection import LineageSourceRow, detect_lineage
from packages.evidence.sql_projector import STATUS_ADMITTED


def _keyword_matches(keyword: str, statement: str) -> bool:
    """Every underscore-separated word in ``keyword`` must appear in ``statement``.

    ``measurement_bias`` matches "screen time measurement relies on self report,
    a clear source of bias" but not "the sample is not representative" -- a
    coarse substring heuristic, not semantic matching, and documented as such
    above.
    """
    words = [word for word in keyword.replace("_", " ").split() if word]
    if not words:
        return False
    lowered = statement.lower()
    return all(word in lowered for word in words)


def _admitted(events: Iterable[LedgerEntry], event_type: str) -> list[LedgerEntry]:
    return [
        entry
        for entry in events
        if entry.event_type == event_type and entry.status == STATUS_ADMITTED
    ]


def _author_names(raw: object) -> tuple[str, ...]:
    """Coerce a payload's untyped ``authors`` value into a tuple of names.

    ``LedgerEntry.payload`` is ``dict[str, object]``, so this value arrives with
    no narrower type than ``object`` -- mirrors the same defensive
    ``isinstance`` pattern already used in
    :meth:`packages.evaluation.harness.SharedLinearMemoryAdapter.load_snapshot`
    rather than trusting an unchecked cast.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(author) for author in raw)


def score_blindspots(
    events: Iterable[LedgerEntry],
    expected_blindspots: tuple[str, ...],
) -> tuple[float, float]:
    """Return ``(recall, precision)`` over admitted Blindspot events.

    Recall: fraction of ``expected_blindspots`` keywords matched by at least
    one admitted blindspot statement. Precision: fraction of admitted
    blindspot statements that matched at least one expected keyword. Both are
    ``0.0`` when there is nothing to compare (no expectations, or no admitted
    blindspots), which is the honest value rather than an undefined one.
    """
    events = list(events)
    statements = [
        str(entry.payload.get("statement", ""))
        for entry in _admitted(events, EvidenceNodeType.BLINDSPOT.value)
    ]
    if not expected_blindspots:
        return (0.0, 0.0)
    matched_statements: set[int] = set()
    hits = 0
    for keyword in expected_blindspots:
        for index, statement in enumerate(statements):
            if _keyword_matches(keyword, statement):
                hits += 1
                matched_statements.add(index)
                break
    recall = hits / len(expected_blindspots)
    precision = len(matched_statements) / len(statements) if statements else 0.0
    return recall, precision


def score_causal_overclaim(events: Iterable[LedgerEntry]) -> float | None:
    """Fraction of admitted causal Claim events that violate the causal policy.

    ``None`` when no causal claim was admitted at all -- an ungated baseline
    that admits everything unconditionally still needs this checked, which is
    exactly why the check is re-run here rather than trusted to the gate.
    """
    claims = [
        entry
        for entry in _admitted(events, EvidenceNodeType.CLAIM.value)
        if entry.payload.get("claim_type") == ClaimType.CAUSAL.value
    ]
    if not claims:
        return None
    violations = sum(
        1
        for entry in claims
        if CausalUpgradePolicy.validate(
            str(entry.payload.get("study_design", "")), ClaimType.CAUSAL
        )
        is not None
    )
    return violations / len(claims)


def score_dissent_preservation(events: Iterable[LedgerEntry]) -> float:
    """Share of dissenting FINAL_JUDGMENT seats with a matching DissentCertificate.

    ``1.0`` when nobody dissented -- there is nothing to have dropped, so a
    task with unanimous seats is not penalised for an opportunity that never
    arose. CLAUDE.md 4 forbids a real dissent going missing, which is what a
    score below ``1.0`` here would mean.
    """
    events = list(events)
    dissenting_seats = {
        str(entry.payload.get("seat"))
        for entry in events
        if entry.event_type == FINAL_JUDGMENT
        and entry.payload.get("has_dissent") is True
    }
    if not dissenting_seats:
        return 1.0
    certificate_authors = {
        str(entry.payload.get("author"))
        for entry in events
        if entry.event_type == EvidenceNodeType.DISSENT_CERTIFICATE.value
    }
    return len(dissenting_seats & certificate_authors) / len(dissenting_seats)


def score_citation_entailment(events: Iterable[LedgerEntry]) -> float | None:
    """Fraction of StudyFinding events whose exact quote entails the claim.

    Runs regardless of admission status: an ungated baseline never gate-checks
    citation entailment before admitting a finding, so measuring only the
    admitted subset would silently exempt exactly the baselines this metric
    exists to expose.
    """
    findings = [
        entry
        for entry in events
        if entry.event_type == EvidenceNodeType.STUDY_FINDING.value
    ]
    if not findings:
        return None
    passed = sum(
        1
        for entry in findings
        if verify_citation_entailment(
            entry.event_id,
            exact_quote=str(entry.payload.get("exact_quote", "")),
        ).passed
    )
    return passed / len(findings)


def score_evidence_independence(events: Iterable[LedgerEntry]) -> float | None:
    """Independent-cluster ratio among admitted Source events.

    ``1.0`` means every admitted source is its own independent cluster;
    lower means sources share a dataset, a preprint lineage, or an extension
    relationship (CLAUDE.md 7.4). There is no ground-truth cluster count to
    grade against here, so this reports the ratio itself rather than an
    "accuracy" against a label nobody has produced yet -- see the module
    docstring.
    """
    sources = _admitted(events, EvidenceNodeType.SOURCE.value)
    if not sources:
        return None
    rows = [
        LineageSourceRow(
            source_id=entry.event_id,
            canonical_doi=(
                str(entry.payload["canonical_doi"])
                if entry.payload.get("canonical_doi")
                else None
            ),
            dataset_id=(
                str(entry.payload["dataset_id"])
                if entry.payload.get("dataset_id")
                else None
            ),
            authors=_author_names(entry.payload.get("authors")),
        )
        for entry in sources
    ]
    dependencies = detect_lineage(rows)
    result = cluster_evidence([row.source_id for row in rows], dependencies)
    return result.independent_cluster_count / result.paper_count


def cost_per_valid_blindspot(
    outcome: BaselineOutcome,
    expected_blindspots: tuple[str, ...],
) -> float | None:
    """Model spend divided by the number of expected blindspots actually found.

    ``None`` when nothing was found -- a cost-per-success ratio with zero
    successes is not a very small number, it is undefined, and CLAUDE.md 10
    forbids presenting a budget-exhausted failure as a suspiciously cheap win.
    """
    recall, _ = score_blindspots(outcome.events, expected_blindspots)
    valid_count = round(recall * len(expected_blindspots))
    if valid_count <= 0:
        return None
    consumed = (
        outcome.budget.limits.model_cost_usd - outcome.budget.model_budget_remaining
    )
    return float(consumed) / valid_count


__all__ = [
    "cost_per_valid_blindspot",
    "score_blindspots",
    "score_causal_overclaim",
    "score_citation_entailment",
    "score_dissent_preservation",
    "score_evidence_independence",
]
