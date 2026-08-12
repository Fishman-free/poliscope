"""Design spec 11.4's ablation ladder, on the scripted demo case.

Each ``full_ablate_*`` variant removes exactly one capability from the full
system; these tests assert that each removal actually changes the run in the
direction the capability is claimed to contribute -- and that the remaining
scores stay honest rather than degrading silently. The numbers here are
mechanism-level checks on a scripted gateway (no model calls), the same
B_dev standing as ``test_evaluation_demo_case.py``; the paper reports them
with that caveat.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evaluation.demo_case import (
    BLINDSPOT_KEYWORDS,
    DemoAcquirer,
    DemoAcquirerNoLineage,
    DemoFindingExtractor,
    DemoGateway,
)
from packages.evaluation.harness import (
    ABLATIONS,
    BaselineOutcome,
    BaselineVariant,
    SharedLinearMemoryAdapter,
    run_baseline,
)
from packages.evaluation.scoring import (
    score_blindspots,
    score_dissent_preservation,
    score_evidence_independence,
)
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.sql_projector import STATUS_ADMITTED


async def _run(
    variant: BaselineVariant,
    *,
    acquirer: DemoAcquirer | None = None,
) -> BaselineOutcome:
    return await run_baseline(
        variant,
        "does reducing adolescent screen time lower depressive symptoms?",
        DemoGateway(),
        acquirer=(
            acquirer
            if acquirer is not None
            else (
                DemoAcquirerNoLineage()
                if variant is BaselineVariant.ABLATE_LINEAGE
                else DemoAcquirer()
            )
        ),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )


def _event_types(outcome: BaselineOutcome) -> set[str]:
    return {event.event_type for event in outcome.events}


async def test_all_six_ablations_keep_the_evidence_gate() -> None:
    """Ablations remove one capability each -- not the whole gate.

    The no-gate rung already exists on the baseline ladder
    (COUNCIL_MEMOBRAIN_NO_GATE); every ablation is "full minus X", so all six
    must still gate.
    """
    from packages.evaluation.harness import _gate_for

    for variant in ABLATIONS:
        assert _gate_for(variant) is not None, variant


async def test_ablate_precommitment_skips_the_phase() -> None:
    full = await _run(BaselineVariant.FULL_POLISCOPE)
    ablated = await _run(BaselineVariant.ABLATE_PRECOMMITMENT)

    assert "PRECOMMITMENT_SEALED" in _event_types(full)
    assert "PRECOMMITMENT_SEALED" not in _event_types(ablated)
    assert len(ablated.events) < len(full.events)
    # A skipped phase is not a silent omission: the report records the gap.
    assert len(ablated.report.phases_run) == len(full.report.phases_run) - 1


async def test_ablate_falsifier_loses_the_specialist_blindspot() -> None:
    """The adversarial falsifier's own blindspot is its unique contribution.

    Its two nominations in the demo case include publication bias -- a gold
    keyword no other seat scripts -- so removing the seat must drop recall,
    while the duplicate it shares with the boundary scientist stops being
    double-paid, so precision rises.
    """
    full = await _run(BaselineVariant.FULL_POLISCOPE)
    ablated = await _run(BaselineVariant.ABLATE_FALSIFIER)

    full_recall, full_precision = score_blindspots(full.events, BLINDSPOT_KEYWORDS)
    ablated_recall, ablated_precision = score_blindspots(
        ablated.events, BLINDSPOT_KEYWORDS
    )
    assert full_recall == 1.0
    assert ablated_recall < full_recall  # publication_bias gold is gone
    assert ablated_precision > full_precision  # no duplicate nomination left
    # Its final-judgment dissent is gone too -- no dissenter, nothing dropped.
    assert score_dissent_preservation(ablated.events) == 1.0


async def test_ablate_auditor_loses_provenance_blindspot() -> None:
    full = await _run(BaselineVariant.FULL_POLISCOPE)
    ablated = await _run(BaselineVariant.ABLATE_AUDITOR)

    full_recall, _ = score_blindspots(full.events, BLINDSPOT_KEYWORDS)
    ablated_recall, _ = score_blindspots(ablated.events, BLINDSPOT_KEYWORDS)
    assert full_recall == 1.0
    # provenance (the auditor's keyword) is unmatched without the seat.
    assert ablated_recall < full_recall


async def test_ablate_dialectical_fold_drops_the_capsule_and_says_so() -> None:
    """Plain fold: no DebateCapsule, and the omission is recorded, not silent.

    The demo scripts a ready joint model with boundary conditions and
    unresolved conflicts, so the full system folds a capsule; the ablation
    skips it and the phase reports the unfilled slot -- CLAUDE.md 4's
    "dissent must not silently vanish" holds even when the mechanism is off.
    """
    full = await _run(BaselineVariant.FULL_POLISCOPE)
    ablated = await _run(BaselineVariant.ABLATE_DIALECTICAL_FOLD)

    capsule_type = EvidenceNodeType.DEBATE_CAPSULE.value
    full_capsules = [
        event
        for event in full.events
        if event.event_type == capsule_type and event.status == STATUS_ADMITTED
    ]
    assert full_capsules, "demo joint modeling must produce a capsule"
    assert capsule_type not in _event_types(ablated)
    assert "JOINT_MODELING:no_capsule_fold" in ablated.report.unfilled_slots


async def test_ablate_lineage_hides_shared_datasets() -> None:
    """No lineage tracking: shared datasets are counted as independent evidence.

    Two of the demo's three sources share a dataset; the full system's
    independence score reflects that (2 clusters from 3 papers), while the
    lineage ablation sees 3 independent papers -- exactly what a system that
    never recorded provenance would report (CLAUDE.md 7.4).
    """
    full = await _run(BaselineVariant.FULL_POLISCOPE)
    ablated = await _run(
        BaselineVariant.ABLATE_LINEAGE, acquirer=DemoAcquirerNoLineage()
    )

    full_independence = score_evidence_independence(full.events)
    ablated_independence = score_evidence_independence(ablated.events)
    assert full_independence == 2 / 3
    assert ablated_independence == 1.0


async def test_ablate_memobrain_shares_one_undifferentiated_transcript() -> None:
    """The memory ablation wires the shared-linear adapter, not per-seat state.

    The scripted case cannot show a downstream score gap (every seat's answers
    are fixed), so the assertion is on the wiring: the adapter is the same
    undifferentiated transcript the linear-context rung uses, which is the
    mechanism CLAUDE.md 3's per-seat privacy rule protects against.
    """
    from packages.evaluation.harness import _memory_for

    task_id = uuid4()
    memory = _memory_for(BaselineVariant.ABLATE_MEMOBRAIN, task_id)
    assert memory is not None
    assert isinstance(memory._adapter, SharedLinearMemoryAdapter)
    # The full system keeps per-seat isolation.
    full_memory = _memory_for(BaselineVariant.FULL_POLISCOPE, task_id)
    assert full_memory is not None
    assert not isinstance(full_memory._adapter, SharedLinearMemoryAdapter)
