"""One end-to-end ForesightBlindspot demo case: FULL_POLISCOPE, scripted top to bottom.

The scripted fixtures live in :mod:`packages.evaluation.demo_case` -- raised
out of this test so the same run also drives ``scripts/arbor_eval.py`` for the
Arbor evaluation loop. This test pins the exact numbers that run must
produce.

**Causal Overclaim is the one score this case cannot produce, honestly.**
``score_causal_overclaim`` reads ``study_design`` off a ``Claim`` event's
payload (see ``packages/evidence/gate.py``'s Stage 6 and ``packages/
evaluation/scoring.py``). The only place that ever emits a ``Claim`` event is
the Fork path in ``packages.council.rounds.registry._fork_events``, and since
Phase 4 it no longer hardcodes ``claim_type="correlational"`` -- a seat that
self-reports ``claim_type``/``study_design`` in its ``fork`` mapping (see that
function's docstring on why self-reporting, not a classifier, is the honest
option here) can produce a genuine causal claim. This demo case's
``DemoGateway`` simply never answers ``CROSS_EXAMINATION`` with any
challenge at all, let alone a fatal one with a ``fork``, so no ``Claim`` event
of any kind is emitted here -- a scenario-specific gap (this scripted run
does not exercise that phase), not the system-wide impossibility it used to
be. This test asserts ``score_causal_overclaim`` returns ``None`` here and
says why, rather than manufacturing a Claim event this particular scripted
run does not produce (CLAUDE.md 7: the system must admit what it does not
know, including about its own evaluation harness). The Fork-produced causal
path itself is covered directly in ``tests/unit/
test_run_cross_examination_fork.py``.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evaluation.demo_case import (
    BLINDSPOT_KEYWORDS,
    DOIS,
    DemoAcquirer,
    DemoFindingExtractor,
    DemoGateway,
)
from packages.evaluation.harness import BaselineVariant, run_baseline
from packages.evaluation.scoring import (
    score_blindspots,
    score_causal_overclaim,
    score_citation_entailment,
    score_dissent_preservation,
    score_evidence_independence,
)
from packages.evidence.contracts import EvidenceNodeType


async def test_full_poliscope_demo_case_produces_real_scores() -> None:
    outcome = await run_baseline(
        BaselineVariant.FULL_POLISCOPE,
        "does reducing adolescent screen time lower depressive symptoms?",
        DemoGateway(),
        acquirer=DemoAcquirer(),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )

    sources = [
        e for e in outcome.events if e.event_type == EvidenceNodeType.SOURCE.value
    ]
    findings = [
        e
        for e in outcome.events
        if e.event_type == EvidenceNodeType.STUDY_FINDING.value
    ]
    blindspots = [
        e for e in outcome.events if e.event_type == EvidenceNodeType.BLINDSPOT.value
    ]
    assert len(sources) == 3
    assert len(findings) == 2  # the third source's extraction failed outright

    recall, precision = score_blindspots(outcome.events, BLINDSPOT_KEYWORDS)
    assert recall == 1.0
    # Nine admitted blindspots: one specialist nomination per seat (the
    # adversarial falsifier submits two, one of which deliberately repeats
    # the boundary scientist's external-validity statement -- see
    # packages/evaluation/demo_case.py's BLINDSPOTS_BY_SEAT), plus the
    # source-diversity check's own flag, which fires because every acquired
    # source carries the same author tuple (see run_acquisition's
    # check_diversity/SourceDiversityInput call). Every keyword is matched by
    # at least one statement, but ``score_blindspots`` counts at most one
    # matched statement per keyword, so precision is 7/9: the diversity flag
    # matches no keyword, and the deliberately duplicated external-validity
    # nomination cannot add a second matched statement -- together they keep
    # precision below 1.0 rather than a fixture artifact.
    assert precision == 7 / 9
    assert len(blindspots) == 9

    entailment = score_citation_entailment(outcome.events)
    assert entailment == 0.5

    independence = score_evidence_independence(outcome.events)
    # Two of three admitted sources share one dataset -> two clusters, three papers.
    assert independence == 2 / 3

    dissent = score_dissent_preservation(outcome.events)
    # The adversarial falsifier's dissent is expected to survive as a
    # DissentCertificate rather than being silently dropped (CLAUDE.md 4).
    assert dissent == 1.0

    # Documented gap, not a bug: see the module docstring. This scripted
    # gateway never answers CROSS_EXAMINATION with a fatal fork, so no Claim
    # event is emitted in this run at all -- a scenario-specific gap, not
    # proof that no run ever could (see test_run_cross_examination_fork.py
    # for the Fork path that now can, since Phase 4).
    assert score_causal_overclaim(outcome.events) is None

    # The demo case is also reachable under the other four baselines (the
    # whole point of the comparison ladder): each configures the same real
    # orchestrator with fewer capabilities, so every variant must complete.
    for variant in (
        BaselineVariant.SINGLE_AGENT,
        BaselineVariant.FIXED_DEBATE,
        BaselineVariant.COUNCIL_LINEAR_CONTEXT,
        BaselineVariant.COUNCIL_MEMOBRAIN_NO_GATE,
    ):
        variant_outcome = await run_baseline(
            variant,
            "does reducing adolescent screen time lower depressive symptoms?",
            DemoGateway(),
            acquirer=DemoAcquirer(),
            finding_extractor=DemoFindingExtractor(),
            confirmed_claims=(uuid4(),),
        )
        assert variant_outcome.report is not None
        assert DOIS and BLINDSPOT_KEYWORDS  # fixtures resolved as expected


async def test_single_agent_emits_exactly_one_final_judgment() -> None:
    """Regression: the single-agent baseline must judge only its one seat.

    The final rejudgment handler used to iterate every seat unconditionally,
    minting six "no initial judgment" placeholder FINAL_JUDGMENT events for
    seats that never participated -- which also let the run claim a seven-seat
    council it did not have. A single-agent run must produce exactly one
    FINAL_JUDGMENT, from the theory builder.
    """
    outcome = await run_baseline(
        BaselineVariant.SINGLE_AGENT,
        "does reducing adolescent screen time lower depressive symptoms?",
        DemoGateway(),
        acquirer=DemoAcquirer(),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )
    judgments = [
        e for e in outcome.events if e.event_type == "FINAL_JUDGMENT"
    ]
    assert len(judgments) == 1
    assert judgments[0].payload["seat"] == "theory_builder"


async def test_full_poliscope_still_emits_seven_final_judgments() -> None:
    """The seven-seat production path is unchanged: all seven seats judge."""
    outcome = await run_baseline(
        BaselineVariant.FULL_POLISCOPE,
        "does reducing adolescent screen time lower depressive symptoms?",
        DemoGateway(),
        acquirer=DemoAcquirer(),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )
    judgments = [
        e for e in outcome.events if e.event_type == "FINAL_JUDGMENT"
    ]
    assert len(judgments) == 7
