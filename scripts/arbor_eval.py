"""ForesightBlindspot eval entry for the Arbor loop.

Usage:
    python scripts/arbor_eval.py --variant full_poliscope [--run-name <node_id>]

Prints one line ``score: <primary>`` (Blindspot F1, maximize) that the Arbor
scoring layer parses, followed by a JSON detail object with every
code-computable score. Smoke-safe by construction: the demo case uses
scripted gateways -- no model calls, no database, no network.

Primary metric: Blindspot F1 (harmonic mean of Blindspot Recall and
Precision), the most direct expression of the "blindspot discovery quality"
objective. All other scores are reported as auxiliary metrics.

Caveat recorded honestly (CLAUDE.md 7): this is the single demo case; the
time-sliced corpus with a dev/test split does not exist yet, so there is no
separate B_test -- every run here is B_dev.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from uuid import uuid4

from packages.evaluation.demo_case import (
    BLINDSPOT_KEYWORDS,
    QUESTION,
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

VARIANTS = {variant.value: variant for variant in BaselineVariant}


def _f1(recall: float, precision: float) -> float:
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


async def _run(variant: BaselineVariant) -> dict[str, object]:
    outcome = await run_baseline(
        variant,
        QUESTION,
        DemoGateway(),
        acquirer=DemoAcquirer(),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )
    recall, precision = score_blindspots(outcome.events, BLINDSPOT_KEYWORDS)
    scores = {
        "variant": variant.value,
        "blindspot_recall": round(recall, 4),
        "blindspot_precision": round(precision, 4),
        "blindspot_f1": round(_f1(recall, precision), 4),
        "citation_entailment": score_citation_entailment(outcome.events),
        "evidence_independence": score_evidence_independence(outcome.events),
        "dissent_preservation": score_dissent_preservation(outcome.events),
        "causal_overclaim": score_causal_overclaim(outcome.events),
        "events": len(outcome.events),
        "model_cost_usd": str(
            outcome.budget.limits.model_cost_usd
            - outcome.budget.model_budget_remaining
        ),
    }
    for key, value in list(scores.items()):
        if isinstance(value, Decimal):
            scores[key] = str(value)
        elif isinstance(value, float):
            scores[key] = round(value, 4)
    return scores


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        default=BaselineVariant.FULL_POLISCOPE.value,
        choices=sorted(VARIANTS),
    )
    parser.add_argument("--run-name", default="", help="Arbor node id, for the record")
    args = parser.parse_args()

    scores = await _run(VARIANTS[args.variant])
    print(f"score: {scores['blindspot_f1']:.4f}")
    print(json.dumps(scores, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
