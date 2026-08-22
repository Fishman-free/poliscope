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
from packages.evaluation.semantic_blindspot import score_blindspots_semantic
from packages.models.contracts import ModelClass, ModelGateway
from packages.models.openai_compatible import OpenAICompatibleModelGateway

VARIANTS = {variant.value: variant for variant in BaselineVariant}


def _f1(recall: float, precision: float) -> float:
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(_mean(values), 4)


def _aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    """Reduce N runs of one variant into mean scores.

    Recall and precision are averaged, then F1 is recomputed from the means
    (the correct reduction: F1 of the means, not the mean of the per-run F1s).
    Constant metrics are averaged over the runs that produced a value (None
    skipped), which is harmless when they are constant. The per-run rows stay
    in the progress file, so the variance is auditable rather than hidden.
    """

    def numeric(key: str) -> list[float]:
        values: list[float] = []
        for run in runs:
            value = run.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
        return values

    first = runs[0]
    recall = _mean(numeric("blindspot_recall"))
    precision = _mean(numeric("blindspot_precision"))
    agg: dict[str, object] = {
        "variant": first["variant"],
        "n_runs": len(runs),
        "blindspot_recall": round(recall, 4),
        "blindspot_precision": round(precision, 4),
        "blindspot_f1": round(_f1(recall, precision), 4),
        "citation_entailment": _mean_or_none(numeric("citation_entailment")),
        "evidence_independence": _mean_or_none(numeric("evidence_independence")),
        "dissent_preservation": _mean_or_none(numeric("dissent_preservation")),
        "causal_overclaim": _mean_or_none(numeric("causal_overclaim")),
        "unfilled_slots": round(_mean(numeric("unfilled_slots")), 1),
        "events": round(_mean(numeric("events")), 1),
    }
    semantic_recall = numeric("blindspot_recall_semantic")
    semantic_precision = numeric("blindspot_precision_semantic")
    if semantic_recall and semantic_precision:
        mean_recall = _mean(semantic_recall)
        mean_precision = _mean(semantic_precision)
        agg["blindspot_recall_semantic"] = round(mean_recall, 4)
        agg["blindspot_precision_semantic"] = round(mean_precision, 4)
        agg["blindspot_f1_semantic"] = round(
            _f1(mean_recall, mean_precision), 4
        )
    else:
        agg["blindspot_recall_semantic"] = None
        agg["blindspot_precision_semantic"] = None
        agg["blindspot_f1_semantic"] = None
    return agg


def _aggregate_table(
    completed: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Group completed per-run rows by variant and aggregate each group."""
    by_variant: dict[str, list[dict[str, object]]] = {}
    for run in completed.values():
        by_variant.setdefault(str(run["variant"]), []).append(run)
    rows = [_aggregate(runs) for runs in by_variant.values()]
    rows.sort(key=lambda row: str(row["variant"]))
    return rows


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


async def _run(
    variant: BaselineVariant,
    gateway: ModelGateway,
    *,
    semantic: bool = False,
) -> dict[str, object]:
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
    if semantic:
        try:
            semantic_scores = await score_blindspots_semantic(
                list(outcome.events), BLINDSPOT_KEYWORDS, gateway
            )
        except RuntimeError as error:
            # The judge is unreachable (e.g. the model endpoint rate-limits).
            # Record the semantic columns as unmeasured and keep the run -- a
            # 429 on one judge call must not throw away a finished run.
            print(f"semantic judge skipped for this run: {error}", flush=True)
            semantic_scores = None
        if semantic_scores is None:
            scores["blindspot_recall_semantic"] = None
            scores["blindspot_precision_semantic"] = None
            scores["blindspot_f1_semantic"] = None
        else:
            semantic_recall, semantic_precision = semantic_scores
            scores["blindspot_recall_semantic"] = round(semantic_recall, 4)
            scores["blindspot_precision_semantic"] = round(semantic_precision, 4)
            scores["blindspot_f1_semantic"] = round(
                _f1(semantic_recall, semantic_precision), 4
            )
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
    has_semantic = any("blindspot_f1_semantic" in row for row in rows)
    has_repeat = any("n_runs" in row for row in rows)
    if has_repeat:
        header += " n |"
        sep += "---|"
    if has_semantic:
        header += " rec_sem | prec_sem | F1_sem |"
        sep += "---|---|---|"
    lines = [header, sep]
    for row in rows:
        line = (
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
        if has_repeat:
            line += " {n} |".format(n=fmt(row.get("n_runs")))
        if has_semantic:
            line += (
                " {rec_sem} | {prec_sem} | {f1_sem} |".format(
                    rec_sem=fmt(row.get("blindspot_recall_semantic")),
                    prec_sem=fmt(row.get("blindspot_precision_semantic")),
                    f1_sem=fmt(row.get("blindspot_f1_semantic")),
                )
            )
        lines.append(line)
    return "\n".join(lines)


def _progress_path(out: str) -> str:
    """The JSONL progress file next to ``--out`` (or a default when unset)."""
    base = out or "live_ablation"
    if base.endswith(".md"):
        base = base[:-3]
    return base + ".progress.jsonl"


def _load_completed(path: str) -> dict[str, dict[str, object]]:
    """Read already-finished runs from the progress file, if any.

    Keyed by ``run_key`` when present (a per-run ``variant#index`` key used
    by the repeat mode), falling back to the bare ``variant`` for the
    single-shot mode, so a resume never skips the second repeat of a variant.
    """
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
            key = row.get("run_key") or row.get("variant")
            if key:
                completed[str(key)] = row
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
        "--variants",
        default="",
        help="comma-separated variant names to run (a subset); takes "
        "precedence over --all when both given",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="runs per variant, averaged into one row (variance reduction)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip runs already recorded in the progress file",
    )
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="add an LLM semantic-judge pass over blindspot recall/precision",
    )
    args = parser.parse_args()

    gateway = _gateway()
    if args.all or args.variants:
        if args.variants:
            chosen = [
                VARIANTS[name.strip()]
                for name in args.variants.split(",")
                if name.strip()
            ]
        else:
            chosen = sorted(BaselineVariant, key=lambda item: item.value)
        progress_path = _progress_path(args.out)
        completed = _load_completed(progress_path) if args.resume else {}
        for variant in chosen:
            for run_index in range(args.repeat):
                key = f"{variant.value}#{run_index}"
                if args.resume and key in completed:
                    print(f"skip {key} (already done)", flush=True)
                    continue
                print(f"running {key} ...", flush=True)
                scores = await _run(variant, gateway, semantic=args.semantic)
                scores["run_key"] = key
                scores["run_index"] = run_index
                completed[key] = scores
                # Persist each run immediately so an interruption never loses
                # a finished run; the aggregate table is rebuilt from what is
                # complete, so a resume continues exactly where it stopped.
                _save_progress(progress_path, completed)
                _write_table(_aggregate_table(completed), args.out)
        rows = _aggregate_table(completed)
        _save_progress(progress_path, completed)
        _write_table(rows, args.out)
        print(_table(rows))
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    scores = await _run(VARIANTS[args.variant], gateway, semantic=args.semantic)
    print(f"score: {scores['blindspot_f1']:.4f}")
    print(json.dumps(scores, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
