"""Variant-differentiation sanity guard (Arbor node 1.2).

The whole point of the ForesightBlindspot five-baseline ladder (design spec
11.3) is that the variants are distinguishable: a lone researcher must not be
able to match a seven-seat council on blindspot coverage, or the comparison
means nothing. This test pins the *structural* property -- single-seat
blindspot coverage is strictly below multi-seat coverage -- rather than exact
scores, which would be brittle to honest material upgrades (the demo material
changed once already: 2 gold keywords became 7, node 2.1).

The guard is deliberately coarse so it can only fail when the comparison is
actually broken: a regression that collapses the ladder back to "every variant
is the same" (the pre-fix 1.1 state, where all five scored 0.8) fails here;
a material upgrade that moves all scores proportionally does not.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evaluation.demo_case import (
    QUESTION,
    DemoAcquirer,
    DemoFindingExtractor,
    DemoGateway,
)
from packages.evaluation.harness import BaselineVariant, run_baseline
from packages.evidence.contracts import EvidenceNodeType


async def _blindspot_count(variant: BaselineVariant) -> int:
    outcome = await run_baseline(
        variant,
        QUESTION,
        DemoGateway(),
        acquirer=DemoAcquirer(),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )
    return sum(
        1
        for event in outcome.events
        if event.event_type == EvidenceNodeType.BLINDSPOT.value
    )


async def test_single_agent_cannot_match_multi_seat_blindspot_coverage() -> None:
    """The ladder's minimum and maximum must be structurally apart.

    The single-agent baseline has one seat and therefore at most one
    specialist nomination plus the source-diversity flag; every seven-seat
    variant receives eight specialist nominations. If the single agent ever
    covers as many blindspots as the council, the seat-restriction mechanism
    (node 1.1) or the material (node 2.1) has regressed.
    """
    single = await _blindspot_count(BaselineVariant.SINGLE_AGENT)
    debate = await _blindspot_count(BaselineVariant.FIXED_DEBATE)
    assert single < debate, (
        f"ladder collapsed: single-agent blindspots ({single}) are not below "
        f"multi-seat blindspots ({debate})"
    )
    assert single == 2  # one specialist nomination + the diversity flag
    assert debate == 9  # eight specialist nominations + the diversity flag
