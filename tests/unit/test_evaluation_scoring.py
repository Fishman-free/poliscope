"""Unit tests for the code-computable ForesightBlindspot scores.

Each function in ``packages.evaluation.scoring`` reuses a production rule
(:class:`CausalUpgradePolicy`, :func:`verify_citation_entailment`,
:func:`detect_lineage`/:func:`cluster_evidence`) rather than re-deriving it, so
these tests build :class:`LedgerEntry` objects directly -- the same shape
:class:`packages.evaluation.harness.EvalLedger` produces -- instead of running
a full council task, keeping each scoring rule isolated from the orchestrator
that would otherwise have to be scripted to exercise it.
"""

from __future__ import annotations

from uuid import uuid4

from packages.council.rounds.registry import FINAL_JUDGMENT
from packages.evaluation.scoring import (
    score_blindspots,
    score_causal_overclaim,
    score_citation_entailment,
    score_dissent_preservation,
    score_evidence_independence,
)
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.ledger import LedgerEntry
from packages.evidence.sql_projector import STATUS_ADMITTED, STATUS_QUARANTINED

_TASK_ID = uuid4()


def _entry(
    event_type: str,
    payload: dict[str, object],
    *,
    status: str = STATUS_ADMITTED,
    sequence: int = 1,
) -> LedgerEntry:
    return LedgerEntry(
        event_id=uuid4(),
        task_id=_TASK_ID,
        event_type=event_type,
        payload=payload,
        idempotency_key=f"key-{sequence}",
        sequence=sequence,
        status=status,
    )


def _blindspot(statement: str) -> LedgerEntry:
    return _entry(EvidenceNodeType.BLINDSPOT.value, {"statement": statement})


# --- score_blindspots ---------------------------------------------------


def test_score_blindspots_no_expectations_is_zero() -> None:
    assert score_blindspots([], ()) == (0.0, 0.0)


def test_score_blindspots_matches_by_keyword() -> None:
    # _keyword_matches is a plain substring heuristic, so the expected keyword's
    # words must literally appear (case-insensitively) in the statement text.
    events = [
        _blindspot("screen time relies on self-report, a clear measurement bias"),
        _blindspot("an unrelated statement about something else entirely"),
    ]
    recall, precision = score_blindspots(events, ("measurement_bias",))
    assert recall == 1.0
    assert precision == 0.5


def test_score_blindspots_no_admitted_blindspots_is_zero_precision() -> None:
    events = [_blindspot("此陈述被隔离")]
    events[0] = LedgerEntry(
        event_id=events[0].event_id,
        task_id=events[0].task_id,
        event_type=events[0].event_type,
        payload=events[0].payload,
        idempotency_key=events[0].idempotency_key,
        sequence=events[0].sequence,
        status=STATUS_QUARANTINED,
    )
    recall, precision = score_blindspots(events, ("measurement_bias",))
    assert recall == 0.0
    assert precision == 0.0


def test_score_blindspots_quarantined_entries_are_excluded() -> None:
    quarantined = _entry(
        EvidenceNodeType.BLINDSPOT.value,
        {"statement": "测量偏差"},
        status=STATUS_QUARANTINED,
    )
    recall, _ = score_blindspots([quarantined], ("measurement_bias",))
    assert recall == 0.0


# --- score_causal_overclaim ----------------------------------------------


def test_score_causal_overclaim_none_when_no_causal_claims() -> None:
    events = [_entry(EvidenceNodeType.CLAIM.value, {"claim_type": "correlational"})]
    assert score_causal_overclaim(events) is None


def test_score_causal_overclaim_flags_disallowed_pair() -> None:
    events = [
        _entry(
            EvidenceNodeType.CLAIM.value,
            {"claim_type": "causal", "study_design": "cross_sectional"},
        )
    ]
    assert score_causal_overclaim(events) == 1.0


def test_score_causal_overclaim_allows_experimental_design() -> None:
    events = [
        _entry(
            EvidenceNodeType.CLAIM.value,
            {"claim_type": "causal", "study_design": "experimental"},
        )
    ]
    assert score_causal_overclaim(events) == 0.0


def test_score_causal_overclaim_ignores_unadmitted_claims() -> None:
    events = [
        _entry(
            EvidenceNodeType.CLAIM.value,
            {"claim_type": "causal", "study_design": "cross_sectional"},
            status=STATUS_QUARANTINED,
        )
    ]
    assert score_causal_overclaim(events) is None


# --- score_dissent_preservation -------------------------------------------


def test_score_dissent_preservation_no_dissent_is_perfect() -> None:
    events = [
        _entry(FINAL_JUDGMENT, {"seat": "theory_builder", "has_dissent": False}),
    ]
    assert score_dissent_preservation(events) == 1.0


def test_score_dissent_preservation_matching_certificate() -> None:
    events = [
        _entry(FINAL_JUDGMENT, {"seat": "theory_builder", "has_dissent": True}),
        _entry(
            EvidenceNodeType.DISSENT_CERTIFICATE.value,
            {"author": "theory_builder"},
        ),
    ]
    assert score_dissent_preservation(events) == 1.0


def test_score_dissent_preservation_missing_certificate_is_zero() -> None:
    events = [
        _entry(FINAL_JUDGMENT, {"seat": "theory_builder", "has_dissent": True}),
    ]
    assert score_dissent_preservation(events) == 0.0


# --- score_citation_entailment --------------------------------------------


def test_score_citation_entailment_none_without_findings() -> None:
    assert score_citation_entailment([]) is None


def test_score_citation_entailment_counts_entailed_quotes() -> None:
    events = [
        _entry(
            EvidenceNodeType.STUDY_FINDING.value,
            {"exact_quote": "a significant association was found"},
        ),
        _entry(EvidenceNodeType.STUDY_FINDING.value, {"exact_quote": ""}),
    ]
    assert score_citation_entailment(events) == 0.5


def test_score_citation_entailment_runs_regardless_of_admission_status() -> None:
    events = [
        _entry(
            EvidenceNodeType.STUDY_FINDING.value,
            {"exact_quote": "a significant association was found"},
            status=STATUS_QUARANTINED,
        ),
    ]
    assert score_citation_entailment(events) == 1.0


# --- score_evidence_independence ------------------------------------------


def test_score_evidence_independence_none_without_sources() -> None:
    assert score_evidence_independence([]) is None


def test_score_evidence_independence_shared_dataset_merges_cluster() -> None:
    events = [
        _entry(EvidenceNodeType.SOURCE.value, {"dataset_id": "cohort-2021"}),
        _entry(EvidenceNodeType.SOURCE.value, {"dataset_id": "cohort-2021"}),
    ]
    assert score_evidence_independence(events) == 0.5


def test_score_evidence_independence_distinct_sources_are_fully_independent() -> None:
    events = [
        _entry(EvidenceNodeType.SOURCE.value, {"dataset_id": "cohort-2021"}),
        _entry(EvidenceNodeType.SOURCE.value, {"dataset_id": "rct-2022"}),
    ]
    assert score_evidence_independence(events) == 1.0


def test_score_evidence_independence_ignores_unadmitted_sources() -> None:
    events = [
        _entry(
            EvidenceNodeType.SOURCE.value,
            {"dataset_id": "cohort-2021"},
            status=STATUS_QUARANTINED,
        ),
    ]
    assert score_evidence_independence(events) is None
