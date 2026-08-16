"""Real-model ablation ladder: five baselines + six ablations on one demo case.

Unlike ``scripts/arbor_eval.py`` (which runs a scripted ``DemoGateway`` with no
model calls), this drives the SAME :func:`packages.evaluation.harness.run_baseline`
through a real :class:`OpenAICompatibleModelGateway`. The tool layer stays
scripted (``DemoAcquirer`` / ``DemoFindingExtractor``) so the comparison isolates
exactly what EpistemoBrain adds -- seat specialisation, per-seat memory, the
collective executive memory, the evidence gate, and the dialectical fold --
without the noise of live web retrieval.

Credentials come from environment variables, never from the repository
(CLAUDE.md 16):

    POLISCOPE_MODEL_API_KEY   the bearer key
    POLISCOPE_MODEL_BASE_URL  the OpenAI-compatible endpoint
    POLISCOPE_MODEL_NAME      the model name

Run:  python scripts/live_ablation.py --variant full_poliscope
      python scripts/live_ablation.py --all --out live_ablation_table.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

from packages.evaluation.demo_case import (
    BLINDSPOT_KEYWORDS,
    QUESTION,
    DemoAcquirer,
    DemoAcquirerNoLineage,
    DemoFindingExtractor,
)
from packages.evaluation.harness import BaselineVariant, run_baseline
from packages.evaluation.scoring import (
    score_blindspots,
    score_causal_overclaim,
    score_citation_entailment,
    score_dissent_preservation,
    score_evidence_independence,
)
from packages.models.contracts import ModelClass
from packages.models.openai_compatible import OpenAICompatibleModelGateway

VARIANTS = {variant.value: variant for variant in BaselineVariant}


def _f1(recall: float, precision: float) -> float:
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def _gateway() -> OpenAICompatibleModelGateway:
    """Build a real gateway from env, raising a clear error when unset."""
    from packages.models.openai_compatible import OpenAICompatibleConfig

    api_key = os.environ.get("POLISCOPE_MODEL_API_KEY")
    base_url = os.environ.get("POLISCOPE_MODEL_BASE_URL")
    model_name = os.environ.get("POLISCOPE_MODEL_NAME")
    if not api_key or not base_url or not model_name:
        raise SystemExit(
            "set POLISCOPE_MODEL_API_KEY / POLISCOPE_MODEL_BASE_URL / "
            "POLISCOPE_MODEL_NAME before running a live ablation"
        )
    config = OpenAICompatibleConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model_names={
            ModelClass.STRONG_REASONING: model_name,
            ModelClass.MEDIUM: model_name,
            ModelClass.LIGHTWEIGHT: model_name,
        },
    )
    return OpenAICompatibleModelGateway(config)


def _acquirer_for(variant: BaselineVariant) -> DemoAcquirer:
    if variant is BaselineVariant.ABLATE_LINEAGE:
        return DemoAcquirerNoLineage()
    return DemoAcquirer()


async def _run(variant: BaselineVariant, gateway: object) -> dict[str, object]:
    outcome = await run_baseline(
        variant,
        QUESTION,
        gateway,
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
            return "-"
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


def _progress_path(out: str) -> str:
    """The JSONL progress file next to ``--out`` (or a default when unset)."""
    base = out or "live_ablation"
    if base.endswith(".md"):
        base = base[:-3]
    return base + ".progress.jsonl"


def _load_completed(path: str) -> dict[str, dict[str, object]]:
    """Read already-finished variants from the progress file, if any."""
    if not os.path.exists(path):
        return {}
    completed: dict[str, dict[str, object]] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            variant = row.get("variant")
            if variant:
                completed[str(variant)] = row
    return completed


def _save_progress(path: str, completed: dict[str, dict[str, object]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in completed.values():
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_table(rows: list[dict[str, object]], out: str) -> None:
    if not out:
        return
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(_table(rows) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        default=BaselineVariant.FULL_POLISCOPE.value,
        choices=sorted(VARIANTS),
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip variants already recorded in the progress file",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    gateway = _gateway()
    if args.all:
        progress_path = _progress_path(args.out)
        completed = _load_completed(progress_path) if args.resume else {}
        rows = list(completed.values())
        for variant in sorted(BaselineVariant, key=lambda item: item.value):
            key = variant.value
            if args.resume and key in completed:
                print(f"skip {key} (already done)", flush=True)
                continue
            print(f"running {key} ...", flush=True)
            scores = await _run(variant, gateway)
            completed[key] = scores
            rows.append(scores)
            # Persist immediately so an interruption never loses a finished
            # variant; the next run resumes with --resume.
            _save_progress(progress_path, completed)
            _write_table(rows, args.out)
        rows.sort(key=lambda row: str(row["variant"]))
        _save_progress(progress_path, completed)
        _write_table(rows, args.out)
        print(_table(rows))
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    scores = await _run(VARIANTS[args.variant], gateway)
    print(f"score: {scores['blindspot_f1']:.4f}")
    print(json.dumps(scores, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
