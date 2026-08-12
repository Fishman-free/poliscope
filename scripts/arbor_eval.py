"""ForesightBlindspot eval entry for the Arbor loop.

Usage:
    python scripts/arbor_eval.py --variant full_poliscope [--run-name <node_id>]
    python scripts/arbor_eval.py --all [--out <table.md>]

Single-variant mode prints one line ``score: <primary>`` (Blindspot F1,
maximize) that the Arbor scoring layer parses, followed by a JSON detail
object with every code-computable score. ``--all`` runs the full ladder --
the five design-spec 11.3 baselines plus the six design-spec 11.4 ablations
-- and prints a markdown comparison table (optionally written to
``--out``), which is the data source for the paper's ablation analysis.

Primary metric: Blindspot F1 (harmonic mean of Blindspot Recall and
Precision), the most direct expression of the "blindspot discovery quality"
objective. All other scores are reported as auxiliary metrics.

Caveat recorded honestly (CLAUDE.md 7): this is the single demo case; the
time-sliced corpus with a dev/test split does not exist yet, so there is no
separate B_test -- every run here is B_dev. The scripted gateway answers
each phase deterministically, so the numbers are mechanism-level checks, not
model-level measurements.
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
    DemoAcquirerNoLineage,
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


def _acquirer_for(variant: BaselineVariant) -> DemoAcquirer:
    """The lineage ablation hands the run a source of sources without
    ``dataset_id``; every other variant uses the full-metadata acquirer."""
    if variant is BaselineVariant.ABLATE_LINEAGE:
        return DemoAcquirerNoLineage()
    return DemoAcquirer()


async def _run(variant: BaselineVariant) -> dict[str, object]:
    outcome = await run_baseline(
        variant,
        QUESTION,
        DemoGateway(),
        acquirer=_acquirer_for(variant),
        finding_extractor=DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )
    recall, precision = score_blindspots(outcome.events, BLINDSPOT_KEYWORDS)
    scores: dict[str, object] = {
        "variant": variant.value,
        "blindspot_recall": round(recall, 4),
        "blindspot_precision": round(precision, 4),
        "blindspot_f1": round(_f1(recall, precision), 4),
        "citation_entailment": score_citation_entailment(outcome.events),
        "evidence_independence": score_evidence_independence(outcome.events),
        "dissent_preservation": score_dissent_preservation(outcome.events),
        "causal_overclaim": score_causal_overclaim(outcome.events),
        "unfilled_slots": len(outcome.report.unfilled_slots),
        "events": len(outcome.events),
        "model_cost_usd": str(
            outcome.budget.limits.model_cost_usd - outcome.budget.model_budget_remaining
        ),
    }
    for key, value in list(scores.items()):
        if isinstance(value, Decimal):
            scores[key] = str(value)
        elif isinstance(value, float):
            scores[key] = round(value, 4)
    return scores


def _table(rows: list[dict[str, object]]) -> str:
    def fmt(value: object) -> str:
        if value is None:
            return "-"  # ASCII-safe dash; some consoles mangle em dash
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    header = (
        "| variant | recall | precision | F1 | entail | independ | dissent "
        "| overclaim | unfilled | events |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        lines.append(
            "| {variant} | {recall} | {precision} | {f1} | {entail} | "
            "{independ} | {dissent} | {overclaim} | {unfilled} | {events} |".format(
                variant=row["variant"],
                recall=fmt(row["blindspot_recall"]),
                precision=fmt(row["blindspot_precision"]),
                f1=fmt(row["blindspot_f1"]),
                entail=fmt(row["citation_entailment"]),
                independ=fmt(row["evidence_independence"]),
                dissent=fmt(row["dissent_preservation"]),
                overclaim=fmt(row["causal_overclaim"]),
                unfilled=fmt(row["unfilled_slots"]),
                events=fmt(row["events"]),
            )
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        default=BaselineVariant.FULL_POLISCOPE.value,
        choices=sorted(VARIANTS),
        help="single variant to run (Arbor mode)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="run all 5 baselines + 6 ablations and print a comparison table",
    )
    parser.add_argument(
        "--out",
        default="",
        help="write the --all table to this file as well as stdout",
    )
    parser.add_argument("--run-name", default="", help="Arbor node id, for the record")
    args = parser.parse_args()

    if args.all:
        rows = []
        for variant in sorted(BaselineVariant, key=lambda item: item.value):
            rows.append(await _run(variant))
        table = _table(rows)
        print(table)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(table + "\n")
        # Compact JSON detail for the record, mirroring single-variant mode.
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    scores = await _run(VARIANTS[args.variant])
    print(f"score: {scores['blindspot_f1']:.4f}")
    print(json.dumps(scores, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
